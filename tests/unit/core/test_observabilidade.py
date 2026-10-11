"""Observabilidade: rastro por turno/follow-up/resumo, etapas por nó, gerações do LLM,
mascaramento de PII e trace_id gravado na mensagem — sem rede (exportador em memória)."""

import json
from uuid import uuid4

import pytest
from langfuse import Langfuse
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from sdr.core.adapters.outbound.agent.agente_qualificador import (
    AgenteQualificador,
    ConfigQualificacao,
    LLMsPorNo,
)
from sdr.core.adapters.outbound.observabilidade.langfuse_rastreio import (
    RastreioLangfuse,
    mascarar_dados,
    usuario_do_lead,
)
from sdr.core.adapters.outbound.observabilidade.llm_observado import LLMObservado
from sdr.core.application.ports.agente import EntradaAgente
from sdr.core.application.ports.llm import MensagemLLM, PapelLLM, RespostaLLM
from sdr.core.application.ports.observabilidade import ContextoRastro
from sdr.core.application.use_cases.processar_turno import ProcessarTurno
from sdr.core.domain.agente import Persona, RespostaAgente
from sdr.core.domain.conversa import Canal, Conversa, Lead, Mensagem, Papel, StatusMensagem
from tests.apoio.fakes import (
    AgendadorFake,
    AgenteRoteirizado,
    CanalFake,
    CatalogoFake,
    ConversaRepositoryFake,
    LeadEventoRepositoryFake,
    LeadRepositoryFake,
    LLMRoteirizado,
    RastreioFake,
    TravaFake,
    entrega_fake,
    texto,
)
from tests.apoio.vertical_fake import INTENCOES, RegrasFake

PERSONA = Persona("Bia", "bia_v3", "PERSONA")


# ---------------------------------------------------------------- adapter Langfuse


class Exportado:
    def __init__(self) -> None:
        self.exportador = InMemorySpanExporter()
        cliente = Langfuse(
            public_key=f"pk-lf-{uuid4().hex}",  # o SDK reaproveita o cliente por chave
            secret_key="sk-lf-teste",
            base_url="http://localhost:9",
            span_exporter=self.exportador,
            tracer_provider=TracerProvider(),
            mask=mascarar_dados,
        )
        self.cliente = cliente
        self.rastreio = RastreioLangfuse(cliente, metadados_fixos={"vertical": "imobiliario"})

    def spans(self) -> dict[str, dict[str, object]]:
        self.cliente.flush()
        return {
            f"{s.attributes.get('langfuse.observation.type')}:{s.name}": dict(s.attributes or {})
            for s in self.exportador.get_finished_spans()
        }


async def test_um_trace_por_turno_com_sessao_usuario_tags_e_pii_mascarada() -> None:
    e = Exportado()
    lead_id = uuid4()
    contexto = ContextoRastro(
        "turno", lead_id, "whatsapp", "atendimento_ia", "compra",
        metadados={"prompt_persona": "lia_v2"},
    )  # fmt: skip
    with e.rastreio.rastrear(contexto) as rastro:
        rastro.entrada = "meu celular é +5511987654321, e-mail ana.souza@gmail.com"
        pedido = [{"role": "user", "content": "CPF 123.456.789-09"}]
        with (
            e.rastreio.etapa("roteador"),
            e.rastreio.geracao("roteador", modelo="gpt-4.1-mini", entrada=pedido) as geracao,
        ):
            geracao.saida, geracao.tokens_entrada, geracao.tokens_saida = "ok", 120, 8
        rastro.saida = "Oi! Vamos achar seu apê."
        rastro.metadados_finais = {"intencao_final": "compra"}

    spans = e.spans()
    turno, no, llm = spans["span:turno"], spans["span:roteador"], spans["generation:roteador"]
    assert rastro.trace_id is not None
    assert len(rastro.trace_id) == 32
    assert turno["session.id"] == str(lead_id)
    assert turno["user.id"] == usuario_do_lead(lead_id) != str(lead_id)
    assert set(turno["langfuse.trace.tags"]) == {  # type: ignore[arg-type]
        "canal:whatsapp", "atendimento:atendimento_ia", "intencao:compra", "vertical:imobiliario",
    }  # fmt: skip
    assert turno["langfuse.trace.metadata.prompt_persona"] == "lia_v2"
    entrada = str(turno["langfuse.observation.input"])
    assert "+55119****4321" in entrada
    assert "a***@gmail.com" in entrada
    assert "987654321" not in entrada
    assert "123.456.789-09" not in str(llm["langfuse.observation.input"])
    assert llm["langfuse.observation.model.name"] == "gpt-4.1-mini"
    assert json.loads(str(llm["langfuse.observation.usage_details"])) == {"input": 120, "output": 8}
    assert no["session.id"] == llm["session.id"] == str(lead_id)  # propagado aos filhos


async def test_erro_no_turno_marca_o_trace_e_propaga() -> None:
    e = Exportado()
    contexto = ContextoRastro("turno", uuid4(), "web", "atendimento_ia")
    with pytest.raises(RuntimeError), e.rastreio.rastrear(contexto):
        raise RuntimeError("LLM fora do ar")
    assert e.spans()["span:turno"]["langfuse.observation.level"] == "ERROR"


# ---------------------------------------------------------------- LLM observado


class LLMQueFalha(LLMRoteirizado):
    async def gerar(self, *args: object, **kwargs: object) -> RespostaLLM:
        raise RuntimeError("timeout")


async def test_llm_observado_registra_geracao_com_modelo_tokens_e_saida() -> None:
    rastreio = RastreioFake()
    llm = LLMObservado(
        LLMRoteirizado(texto("Oi!", tokens=42), estruturadas=[{"intencao": "compra"}]),
        rastreio,
        "roteador",
    )
    mensagens = [MensagemLLM(PapelLLM.USUARIO, "oi")]
    await llm.gerar(mensagens)
    await llm.gerar_estruturado(mensagens, {"type": "object"}, "roteamento")

    (_, nome, _, livre), (_, _, _, estruturada) = rastreio.observacoes
    assert nome == "roteador"
    assert (livre.saida, livre.tokens_entrada) == ("Oi!", 42)
    assert estruturada.saida == {"intencao": "compra"}


async def test_llm_observado_marca_erro_e_propaga() -> None:
    rastreio = RastreioFake()
    llm = LLMObservado(LLMQueFalha(), rastreio, "agente")
    with pytest.raises(RuntimeError):
        await llm.gerar([MensagemLLM(PapelLLM.USUARIO, "oi")])
    [(_, _, _, observacao)] = rastreio.observacoes
    assert observacao.erro == "RuntimeError: timeout"


# ---------------------------------------------------------------- grafo: etapa por nó


async def test_cada_no_do_grafo_vira_etapa_com_as_geracoes_dentro() -> None:
    rastreio = RastreioFake()
    roteador = LLMObservado(
        LLMRoteirizado(estruturadas=[{"intencao": "indefinida", "confianca": 0.9}]),
        rastreio,
        "roteador",
    )
    agente = LLMObservado(LLMRoteirizado(texto("Me conta mais?")), rastreio, "agente")
    grafo = AgenteQualificador(
        LLMsPorNo(roteador, LLMRoteirizado(), agente),
        PERSONA,
        ConfigQualificacao(INTENCOES, RegrasFake(), "DESCOBERTA"),
        ferramentas=[],
        rastreio=rastreio,
    )
    lead = Lead.novo(Canal.WEB, "lead-1")
    with rastreio.rastrear(ContextoRastro("turno", lead.id, "web", "atendimento_ia")):
        await grafo.responder(EntradaAgente(lead=lead, historico=[], texto="oi"))

    assert rastreio.nomes("span") == ["gate", "roteador", "descoberta"]
    geracoes = {(nome, pai) for t, nome, pai, _ in rastreio.observacoes if t == "generation"}
    assert geracoes == {("roteador", "roteador"), ("agente", "descoberta")}
    [(_, _, pai, _)] = [o for o in rastreio.observacoes if o[1] == "gate"]
    assert pai == "turno"


# ---------------------------------------------------------------- turno: trace na mensagem


async def test_turno_abre_um_rastro_e_grava_o_trace_na_resposta() -> None:
    leads, conversas, eventos = (
        LeadRepositoryFake(),
        ConversaRepositoryFake(),
        LeadEventoRepositoryFake(),
    )
    rastreio = RastreioFake()
    turno = ProcessarTurno(
        leads, conversas, AgenteRoteirizado(RespostaAgente("Oi! Comprar ou alugar?")),
        CatalogoFake(), eventos=eventos, persona=PERSONA, trava=TravaFake(),
        agendador=AgendadorFake(), entrega=entrega_fake(conversas, eventos, CanalFake()),
        rastreio=rastreio,
    )  # fmt: skip
    lead = Lead.novo(Canal.WHATSAPP, "+5511987654321")
    leads.leads[lead.id] = lead
    conversa = Conversa.nova(lead)
    conversas.conversas[conversa.id] = conversa
    for fala in ("oi", "quero um apê"):
        conversas.mensagens.append(
            Mensagem.nova(conversa.id, Papel.LEAD, fala, status=StatusMensagem.PENDENTE)
        )

    resultado = await turno.executar(lead.id)

    assert resultado is not None
    [(contexto, rastro)] = rastreio.rastros  # um rastro para o turno inteiro (lote agregado)
    assert (contexto.nome, contexto.lead_id, contexto.canal) == ("turno", lead.id, "whatsapp")
    assert contexto.estado_atendimento == "atendimento_ia"
    assert contexto.metadados["prompt_persona"] == "bia_v3"
    assert rastro.entrada == "oi\nquero um apê"
    assert rastro.saida == "Oi! Comprar ou alugar?"
    assert resultado.resposta.metadados["trace"] == {"id": "trace-1", "url": "https://lf/t"}
