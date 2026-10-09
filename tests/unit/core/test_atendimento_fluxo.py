"""Atendimento humano ponta a ponta sem banco/LLM real: gate do grafo, turno e rotas."""

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta

import httpx

from sdr.core.adapters.inbound.http.app import criar_app
from sdr.core.adapters.outbound.agent.agente_qualificador import (
    AgenteQualificador,
    ConfigQualificacao,
    LLMsPorNo,
)
from sdr.core.application.ports.agente import EntradaAgente
from sdr.core.application.use_cases.atendimento import (
    AplicarAcaoAtendimento,
    AssumirAtendimento,
    DevolverAtendimento,
    EnviarMensagemResponsavel,
    ListarAtendimentos,
)
from sdr.core.application.use_cases.processar_turno import ProcessarTurno
from sdr.core.domain.agente import Persona, RespostaAgente
from sdr.core.domain.atendimento import (
    AcaoAtendimento,
    Atendimento,
    EstadoAtendimento,
    HorarioAtendimento,
    MotivoHandoff,
    TipoAcaoAtendimento,
)
from sdr.core.domain.conversa import Canal, Conversa, Lead, Mensagem, Papel, StatusMensagem
from sdr.core.domain.eventos import TipoEvento
from tests.apoio.fakes import (
    FUSO_SP,
    AgendadorFake,
    AgenteRoteirizado,
    CanalFake,
    CatalogoFake,
    ConversaRepositoryFake,
    LeadEventoRepositoryFake,
    LeadRepositoryFake,
    LLMRoteirizado,
    RelogioFake,
    TravaFake,
    entrega_fake,
    template_teste,
    texto,
)
from tests.apoio.http import criar_dependencias
from tests.apoio.vertical_fake import INTENCOES, RegrasFake

E = EstadoAtendimento
HORARIO = HorarioAtendimento.de_texto("seg-sex", "09:00-18:00", "America/Sao_Paulo")
SEGUNDA_10H = datetime(2026, 10, 5, 10, tzinfo=FUSO_SP)
SEXTA_19H = datetime(2026, 10, 9, 19, tzinfo=FUSO_SP)
PERSONA = Persona(nome="Bia", versao_prompt="v1", prompt_sistema="PERSONA_BIA")


def lead_em(estado: EstadoAtendimento, **extra: object) -> Lead:
    lead = Lead.novo(Canal.WEB, "lead-humano")
    atendimento = Atendimento(lead.id, estado, SEGUNDA_10H, na_fila_desde=SEGUNDA_10H, **extra)  # type: ignore[arg-type]
    return replace(lead, atendimento=atendimento)


class Grafo:
    def __init__(
        self,
        roteador: list[dict[str, object]] | None = None,
        classificacoes: list[dict[str, object]] | None = None,
        agora: datetime = SEGUNDA_10H,
    ) -> None:
        self.roteador = LLMRoteirizado(estruturadas=roteador or [])
        self.extracao = LLMRoteirizado(estruturadas=classificacoes or [])
        self.agente = LLMRoteirizado(texto("ok"))
        self.grafo = AgenteQualificador(
            LLMsPorNo(self.roteador, self.extracao, self.agente),
            PERSONA,
            ConfigQualificacao(INTENCOES, RegrasFake(), "PROMPT_DESCOBERTA"),
            ferramentas=[],
            fuso=FUSO_SP,
            relogio=RelogioFake(agora),
            horario=HORARIO,
        )

    async def responder(self, lead: Lead, fala: str = "oi?") -> RespostaAgente:
        return await self.grafo.responder(EntradaAgente(lead, [], fala))

    def bloco(self) -> str:
        return next(
            m.conteudo for m in self.agente.chamadas[-1][0] if m.conteudo.startswith("[Atendimento")
        )


# ---------------------------------------------------------------- gate do grafo


async def test_em_atendimento_humano_a_ia_fica_em_silencio_sem_chamar_llm() -> None:
    g = Grafo()
    resposta = await g.responder(lead_em(E.ATENDIMENTO_HUMANO, responsavel="Rafael"))
    assert resposta.silenciar is True
    assert g.roteador.chamadas_estruturadas == g.agente.chamadas == []


async def test_pedido_de_humano_vira_pergunta_de_confirmacao() -> None:
    g = Grafo(
        roteador=[
            {"intencao": "indefinida", "confianca": 0.9, "atendimento_humano": "pedido_explicito"}
        ]
    )

    resposta = await g.responder(lead_em(E.ATENDIMENTO_IA), "quero falar com uma pessoa")

    assert resposta.acao_atendimento == AcaoAtendimento(
        TipoAcaoAtendimento.SOLICITAR_HANDOFF, motivo=MotivoHandoff.PEDIDO_EXPLICITO
    )
    assert resposta.metadados["atendimento"]["estado_previsto"] == "confirmando_handoff"  # type: ignore[index]
    assert "se ele quer ser transferido para uma pessoa da equipe" in g.bloco()
    assert "FORA DO HORÁRIO" not in g.bloco()
    assert g.extracao.chamadas_estruturadas == []  # não extrai ficha nesse turno


async def test_fora_do_horario_a_confirmacao_ja_informa_horario_e_retorno() -> None:
    g = Grafo(
        roteador=[{"intencao": "indefinida", "confianca": 0.9, "atendimento_humano": "frustracao"}],
        agora=SEXTA_19H,
    )
    await g.responder(lead_em(E.ATENDIMENTO_IA), "ninguém me entende aqui!")

    bloco = g.bloco()
    assert "parece frustrado" in bloco
    assert "de segunda a sexta, das 9h às 18h" in bloco
    assert "volta segunda (12/10) às 9h" in bloco
    assert "enquanto isso você pode seguir ajudando" in bloco
    assert "UMA pergunta de sim/não" in bloco


async def test_confirmacao_sim_nao_e_ambigua() -> None:
    for resposta, tipo_esperado in [("sim", True), ("nao", False), ("ambigua", None)]:
        g = Grafo(classificacoes=[{"resposta": resposta}])
        r = await g.responder(lead_em(E.CONFIRMANDO_HANDOFF), "hum")
        assert r.acao_atendimento == AcaoAtendimento(
            TipoAcaoAtendimento.RESPONDER_HANDOFF, confirmado=tipo_esperado
        )
    assert "Pergunte de novo" in g.bloco()


async def test_na_fila_informa_espera_e_oferece_voltar_sem_prometer_tempo() -> None:
    g = Grafo(classificacoes=[{"quer_voltar_para_assistente": False}], agora=SEXTA_19H)

    r = await g.responder(lead_em(E.AGUARDANDO_HUMANO), "oi?")

    assert r.acao_atendimento == AcaoAtendimento(TipoAcaoAtendimento.MENSAGEM_NA_ESPERA)
    bloco = g.bloco()
    assert "continua na fila" in bloco
    assert "voltar a conversar com a assistente virtual" in bloco
    assert "volta segunda (12/10) às 9h" in bloco
    prompt = g.agente.chamadas[0][0][0].conteudo
    assert "Nunca prometa tempo de espera" in prompt
    assert "varie a forma" in prompt


async def test_na_fila_querendo_voltar_pede_confirmacao() -> None:
    g = Grafo(classificacoes=[{"quer_voltar_para_assistente": True}])
    r = await g.responder(lead_em(E.AGUARDANDO_HUMANO), "deixa, pode ser com você")
    assert r.acao_atendimento == AcaoAtendimento(TipoAcaoAtendimento.SOLICITAR_RETORNO_IA)
    assert "SAI da fila" in g.bloco()


async def test_mensagens_da_equipe_entram_no_contexto_da_ia() -> None:
    g = Grafo(roteador=[{"intencao": "indefinida", "confianca": 0.9, "atendimento_humano": "nao"}])
    lead = lead_em(E.ATENDIMENTO_IA)
    historico = [
        Mensagem.nova(lead.id, Papel.LEAD, "o condomínio é caro?"),
        Mensagem.nova(
            lead.id,
            Papel.RESPONSAVEL,
            "É R$ 450 por mês.",
            metadados={"responsavel": "Rafael"},
        ),
    ]
    await g.grafo.responder(EntradaAgente(lead, historico, "e o IPTU?"))

    contexto = [m.conteudo for m in g.agente.chamadas[0][0]]
    assert "[Rafael, da equipe, escreveu ao lead neste ponto da conversa]: É R$ 450 por mês." in (
        contexto
    )
    assert "Equipe: É R$ 450 por mês." in g.roteador.chamadas_estruturadas[0][0][1].conteudo


# ---------------------------------------------------------------- turno


class Turno:
    def __init__(self, lead: Lead, *respostas: RespostaAgente) -> None:
        self.leads = LeadRepositoryFake()
        self.conversas = ConversaRepositoryFake()
        self.eventos = LeadEventoRepositoryFake()
        self.canal = CanalFake()
        self.relogio = RelogioFake(SEGUNDA_10H)
        self.agente = AgenteRoteirizado(*respostas)
        self.turno = ProcessarTurno(
            self.leads,
            self.conversas,
            self.agente,
            CatalogoFake(),
            eventos=self.eventos,
            persona=PERSONA,
            trava=TravaFake(),
            agendador=AgendadorFake(),
            entrega=entrega_fake(self.conversas, self.eventos, self.canal, self.relogio),
            atendimento=AplicarAcaoAtendimento.com(self.leads, self.eventos, self.relogio),
        )
        self.lead = lead
        self.leads.leads[lead.id] = lead
        self.conversa = Conversa.nova(lead)
        self.conversas.conversas[self.conversa.id] = self.conversa

    def chega(self, fala: str) -> None:
        self.conversas.mensagens.append(
            Mensagem.nova(self.conversa.id, Papel.LEAD, fala, status=StatusMensagem.PENDENTE)
        )

    def estado(self) -> EstadoAtendimento:
        return self.leads.leads[self.lead.id].atendimento_atual.estado

    def assumir(self) -> AssumirAtendimento:
        return AssumirAtendimento(self.leads, self.eventos, self.relogio)


async def test_turno_silencioso_registra_o_lote_sem_responder() -> None:
    t = Turno(lead_em(E.ATENDIMENTO_HUMANO), RespostaAgente("", silenciar=True))
    t.chega("oi, Rafael?")

    assert await t.turno.executar(t.lead.id) is None

    assert t.conversas.textos(StatusMensagem.PROCESSADA) == ["oi, Rafael?"]
    assert t.canal.enviadas == []


async def test_acao_do_turno_e_aplicada_e_gravada_com_eventos() -> None:
    acao = AcaoAtendimento(TipoAcaoAtendimento.RESPONDER_HANDOFF, confirmado=True)
    t = Turno(
        lead_em(E.CONFIRMANDO_HANDOFF), RespostaAgente("Você está na fila!", acao_atendimento=acao)
    )
    t.chega("sim")

    resultado = await t.turno.executar(t.lead.id)

    assert resultado is not None
    assert t.estado() is E.AGUARDANDO_HUMANO
    assert [e.tipo for e in t.eventos.eventos] == [TipoEvento.HANDOFF_CONFIRMADO]
    assert [m.texto for _, m in t.canal.enviadas] == ["Você está na fila!"]


async def test_humano_assumiu_durante_o_processamento_descarta_a_resposta() -> None:
    lead = lead_em(E.AGUARDANDO_HUMANO)
    acao = AcaoAtendimento(TipoAcaoAtendimento.MENSAGEM_NA_ESPERA)
    t = Turno(lead)
    assumir = t.assumir()

    async def responder_enquanto_humano_assume(entrada: EntradaAgente) -> RespostaAgente:
        await assumir.executar(Canal.WEB, "lead-humano", "Rafael")  # chega no meio do turno
        await asyncio.sleep(0)
        return RespostaAgente("Você continua na fila…", acao_atendimento=acao)

    t.agente.responder = responder_enquanto_humano_assume  # type: ignore[method-assign]
    t.chega("oi?")

    assert await t.turno.executar(lead.id) is None

    assert t.estado() is E.ATENDIMENTO_HUMANO  # o turno não sobrescreveu o humano
    assert t.canal.enviadas == []  # a resposta da IA foi descartada
    assert t.conversas.textos(StatusMensagem.ENVIADA) == []
    assert t.conversas.textos(StatusMensagem.PROCESSADA) == ["oi?"]
    assert [e.tipo for e in t.eventos.eventos] == [TipoEvento.ATENDIMENTO_HUMANO_INICIADO]


async def test_turno_nao_apaga_atendimento_gravado_por_outro_ator() -> None:
    t = Turno(lead_em(E.ATENDIMENTO_IA), RespostaAgente("Oi!"))
    t.chega("oi")
    await t.turno.executar(t.lead.id)
    assert t.estado() is E.ATENDIMENTO_IA


# ---------------------------------------------------------------- equipe (casos de uso + HTTP)


class Equipe:
    def __init__(self, lead: Lead) -> None:
        self.t = Turno(lead)
        deps = (self.t.leads, self.t.eventos, self.t.relogio)
        self.app = criar_app(
            criar_dependencias(
                listar_atendimentos=lambda: ListarAtendimentos(self.t.leads, self.t.relogio),
                assumir_atendimento=lambda: AssumirAtendimento(*deps),
                devolver_atendimento=lambda: DevolverAtendimento(*deps),
                enviar_mensagem_responsavel=lambda: EnviarMensagemResponsavel(
                    self.t.leads,
                    self.t.conversas,
                    entrega_fake(self.t.conversas, self.t.eventos, self.t.canal, self.t.relogio),
                    template_teste("retomada_equipe"),
                ),
            )
        )

    def http(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://t")


def lead_falou(eq: "Equipe", fala: str = "quero falar com uma pessoa") -> None:
    eq.t.conversas.mensagens.append(
        Mensagem.nova(eq.t.conversa.id, Papel.LEAD, fala, criada_em=eq.t.relogio.agora())
    )


async def test_fila_assumir_responder_e_devolver_pela_api() -> None:
    eq = Equipe(lead_em(E.AGUARDANDO_HUMANO))
    lead_falou(eq)
    eq.t.relogio.avancar(minutes=4)
    async with eq.http() as http:
        fila = (await http.get("/atendimentos/fila")).json()
        assert [(i["lead_id"], i["espera_segundos"]) for i in fila] == [("lead-humano", 240)]

        antes = await http.post("/atendimentos/lead-humano/mensagens", json={"texto": "oi"})
        assert antes.status_code == 409  # ainda não assumiu

        assumido = await http.post(
            "/atendimentos/lead-humano/assumir", json={"responsavel": "Rafael"}
        )
        assert assumido.json()["estado"] == "atendimento_humano"
        assert (await http.get("/atendimentos/fila")).json() == []
        em_atendimento = (await http.get("/atendimentos")).json()
        assert em_atendimento[0]["atendimento"]["responsavel"] == "Rafael"

        enviada = await http.post(
            "/atendimentos/lead-humano/mensagens", json={"texto": "Condomínio de R$ 450."}
        )
        assert enviada.status_code == 201
        assert enviada.json()["papel"] == "responsavel"
        assert enviada.json()["envio"] == "texto"  # dentro da janela de 24h

        devolvido = await http.post("/atendimentos/lead-humano/devolver")
        assert devolvido.json()["estado"] == "atendimento_ia"
        de_novo = await http.post("/atendimentos/lead-humano/devolver")
        assert de_novo.status_code == 409
        assert (await http.post("/atendimentos/ninguem/devolver")).status_code == 404

    _, mensagem = eq.t.canal.enviadas[0]
    assert (mensagem.papel, mensagem.metadados["responsavel"]) == (Papel.RESPONSAVEL, "Rafael")
    assert [e.tipo for e in eq.t.eventos.eventos] == [
        TipoEvento.ATENDIMENTO_HUMANO_INICIADO,
        TipoEvento.ATENDIMENTO_HUMANO_ENCERRADO,
    ]


async def test_assumir_quem_nao_esta_na_fila_e_conflito() -> None:
    eq = Equipe(lead_em(E.ATENDIMENTO_IA))
    async with eq.http() as http:
        r = await http.post("/atendimentos/lead-humano/assumir", json={"responsavel": "Rafael"})
    assert r.status_code == 409
    assert "atendimento_ia → atendimento_humano" in r.json()["detail"]


async def test_fila_ordenada_por_tempo_de_espera() -> None:
    eq = Equipe(lead_em(E.AGUARDANDO_HUMANO))
    novo = Lead.novo(Canal.WEB, "lead-antigo")
    dez_min_antes = SEGUNDA_10H - timedelta(minutes=10)
    mais_antigo = replace(
        novo,
        atendimento=Atendimento(novo.id, E.AGUARDANDO_HUMANO, na_fila_desde=dez_min_antes),
    )
    eq.t.leads.leads[mais_antigo.id] = mais_antigo
    fila = await ListarAtendimentos(eq.t.leads, eq.t.relogio).executar()
    assert [i.lead.remetente_id for i in fila] == ["lead-antigo", "lead-humano"]


async def test_resposta_da_equipe_fora_da_janela_sai_por_template() -> None:
    eq = Equipe(lead_em(E.ATENDIMENTO_HUMANO))
    lead_falou(eq)
    eq.t.relogio.avancar(hours=25)
    async with eq.http() as http:
        enviada = await http.post(
            "/atendimentos/lead-humano/mensagens",
            json={"texto": "Condomínio de R$ 450.", "responsavel": "Rafael"},
        )
    assert enviada.json()["envio"] == "template"
    assert eq.t.canal.enviadas == []  # texto livre não sai fora da janela
    [(_, nome, variaveis)] = eq.t.canal.templates
    assert nome == "retomada_equipe"
    assert variaveis == {"primeiro_nome": "tudo bem", "gancho": "Lembrei de você."}
    mensagem = eq.t.conversas.mensagens[-1]
    assert mensagem.texto == "Oi, tudo bem! Lembrei de você. Responda quando puder."
    assert mensagem.metadados["envio"]["texto_original"] == "Condomínio de R$ 450."  # type: ignore[index]
