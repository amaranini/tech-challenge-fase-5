"""Grafo genérico com vertical fake e LLMs roteirizados (um por nó)."""

from dataclasses import replace

from sdr.core.adapters.outbound.agent.agente_qualificador import (
    AgenteQualificador,
    ConfigQualificacao,
    LLMsPorNo,
)
from sdr.core.application.ferramentas.buscar_catalogo import FerramentaBuscarCatalogo
from sdr.core.application.ports.agente import EntradaAgente
from sdr.core.application.ports.llm import DefinicaoFerramenta, PapelLLM
from sdr.core.domain.agente import Persona
from sdr.core.domain.conversa import Canal, Conversa, Lead, Mensagem, Papel
from sdr.core.domain.eventos import TipoEvento
from sdr.core.domain.qualificacao import Qualificacao
from tests.apoio.fakes import CatalogoFake, LLMRoteirizado, chamar, item, texto
from tests.apoio.vertical_fake import INTENCOES, RegrasFake

PERSONA = Persona(nome="Bia", versao_prompt="v1", prompt_sistema="PERSONA_BIA")
DESCOBERTA = "PROMPT_DESCOBERTA"


class Cenario:
    def __init__(
        self,
        roteador: list[dict[str, object]],
        extracao: list[dict[str, object]] | None = None,
        agente: list | None = None,  # type: ignore[type-arg]
        catalogo: CatalogoFake | None = None,
    ) -> None:
        self.roteador = LLMRoteirizado(estruturadas=roteador)
        self.extracao = LLMRoteirizado(estruturadas=extracao or [])
        self.agente = LLMRoteirizado(*(agente or [texto("ok")]))
        ferramenta = FerramentaBuscarCatalogo(
            catalogo or CatalogoFake(), DefinicaoFerramenta("buscar", "Busca", {})
        )
        self.grafo = AgenteQualificador(
            LLMsPorNo(self.roteador, self.extracao, self.agente),
            PERSONA,
            ConfigQualificacao(INTENCOES, RegrasFake(), DESCOBERTA),
            ferramentas=[ferramenta],
        )


def entrada(
    lead: Lead, texto_lead: str = "oi", historico: list[Mensagem] | None = None
) -> EntradaAgente:
    return EntradaAgente(lead=lead, historico=historico or [], texto=texto_lead)


def extracao(campos: dict[str, object], corrigidos: list[str] | None = None) -> dict[str, object]:
    return {"campos": campos, "campos_corrigidos": corrigidos or [], "campos_removidos": []}


LEAD = Lead.novo(Canal.WEB, "lead-1")


async def test_intencao_indefinida_vai_para_descoberta_sem_extracao() -> None:
    cenario = Cenario(roteador=[{"intencao": "indefinida", "confianca": 0.9}])

    resposta = await cenario.grafo.responder(entrada(LEAD))

    sistema = cenario.agente.chamadas[0][0][0].conteudo
    assert "PERSONA_BIA" in sistema
    assert DESCOBERTA in sistema
    assert cenario.extracao.chamadas_estruturadas == []
    assert resposta.metadados["no_resposta"] == "descoberta"
    assert resposta.qualificacao is not None
    assert resposta.qualificacao.intencao_atual is None
    assert resposta.eventos == ()


async def test_roteador_recebe_intencoes_da_vertical_e_indefinida() -> None:
    cenario = Cenario(roteador=[{"intencao": "indefinida", "confianca": 0.9}])

    await cenario.grafo.responder(entrada(LEAD, "quero treinar"))

    mensagens, schema, _ = cenario.roteador.chamadas_estruturadas[0]
    assert schema["properties"]["intencao"]["enum"] == ["plano", "avulso", "indefinida"]  # type: ignore[index]
    assert "- plano: quer assinar um plano" in mensagens[0].conteudo
    assert mensagens[1].conteudo.endswith("Lead: quero treinar")


async def test_fluxo_completo_extrai_pontua_e_pergunta_o_proximo_campo() -> None:
    cenario = Cenario(
        roteador=[{"intencao": "plano", "confianca": 0.95}],
        extracao=[extracao({"unidade": "Centro", "horario": 9, "inventado": "x"})],
    )

    resposta = await cenario.grafo.responder(entrada(LEAD, "quero plano no Centro"))

    q = resposta.qualificacao
    assert q is not None
    assert q.intencao_atual == "plano"
    assert q.ficha == {"unidade": "Centro"}  # horario inválido (int) e campo inventado descartados
    assert q.score is not None
    assert q.score.pontos == 25
    assert resposta.campos_faltantes == ("orcamento", "horario")
    assert [e.tipo for e in resposta.eventos] == [
        TipoEvento.INTENCAO_IDENTIFICADA,
        TipoEvento.CAMPO_PREENCHIDO,
        TipoEvento.SCORE_ALTERADO,
    ]
    mensagens = cenario.agente.chamadas[0][0]
    assert "PROMPT_PLANO" in mensagens[0].conteudo
    bloco = next(m.conteudo for m in mensagens if "Estado da qualificação" in m.conteudo)
    assert "Próximo dado a descobrir: orcamento (descrição de orcamento)" in bloco
    assert "NO MÁXIMO UMA pergunta" in bloco
    assert "Outros dados faltantes (não pergunte agora): horario" in bloco


async def test_qualifica_e_informa_proxima_acao_ao_especialista() -> None:
    lead = replace(
        LEAD,
        qualificacao=Qualificacao(
            LEAD.id,
            intencao_atual="plano",
            fichas={"plano": {"unidade": "Centro", "orcamento": 150}},
        ),
    )
    cenario = Cenario(
        roteador=[{"intencao": "plano", "confianca": 0.9}],
        extracao=[extracao({"horario": "noite"})],
    )

    resposta = await cenario.grafo.responder(entrada(lead, "à noite"))

    assert resposta.qualificacao is not None
    assert resposta.qualificacao.proxima_acao == "agendar_aula"
    assert TipoEvento.LEAD_QUALIFICADO in [e.tipo for e in resposta.eventos]
    bloco = next(
        m.conteudo for m in cenario.agente.chamadas[0][0] if "Estado da qualificação" in m.conteudo
    )
    assert "Lead QUALIFICADO. Próxima ação: agendar_aula" in bloco


async def test_troca_de_intencao_no_meio_da_conversa() -> None:
    lead = replace(
        LEAD,
        qualificacao=Qualificacao(
            LEAD.id, intencao_atual="plano", fichas={"plano": {"unidade": "Centro"}}
        ),
    )
    cenario = Cenario(
        roteador=[{"intencao": "avulso", "confianca": 0.85}],
        extracao=[extracao({"data": "sábado"})],
    )

    resposta = await cenario.grafo.responder(entrada(lead, "na verdade só quero uma diária sábado"))

    q = resposta.qualificacao
    assert q is not None
    assert q.intencao_atual == "avulso"
    assert q.fichas["avulso"] == {"unidade": "Centro", "data": "sábado"}
    assert q.fichas["plano"] == {"unidade": "Centro"}
    assert resposta.eventos[0].tipo is TipoEvento.INTENCAO_ALTERADA
    assert "PROMPT_AVULSO" in cenario.agente.chamadas[0][0][0].conteudo
    _, schema, nome = cenario.extracao.chamadas_estruturadas[0]
    assert nome == "ficha_avulso"
    assert set(schema["properties"]["campos"]["properties"]) == {"unidade", "data"}  # type: ignore[index]


async def test_resposta_curta_mantem_intencao_mesmo_com_roteador_indefinido() -> None:
    lead = replace(
        LEAD, qualificacao=Qualificacao(LEAD.id, intencao_atual="plano", fichas={"plano": {}})
    )
    cenario = Cenario(
        roteador=[{"intencao": "indefinida", "confianca": 0.4}],
        extracao=[extracao({"orcamento": 120})],
    )

    resposta = await cenario.grafo.responder(entrada(lead, "uns 120"))

    assert resposta.qualificacao is not None
    assert resposta.qualificacao.ficha == {"orcamento": 120}
    assert resposta.metadados["no_resposta"] == "especialista"


async def test_roteador_com_saida_invalida_e_tratado_como_indefinida() -> None:
    cenario = Cenario(roteador=[{"intencao": "voar", "confianca": "alta"}])
    resposta = await cenario.grafo.responder(entrada(LEAD))
    assert resposta.metadados["roteamento"] == {
        "classificada": "indefinida",
        "confianca": 0.0,
        "modelo": "fake-1",
    }


async def test_especialista_usa_ferramenta_e_volta_com_resultado() -> None:
    cenario = Cenario(
        roteador=[{"intencao": "plano", "confianca": 0.9}],
        extracao=[extracao({})],
        agente=[chamar("buscar", texto="plano"), texto("Temos o A-1!")],
        catalogo=CatalogoFake(item("A-1")),
    )

    resposta = await cenario.grafo.responder(entrada(LEAD))

    assert resposta.texto == "Temos o A-1!"
    assert [i.id for i in resposta.itens_consultados] == ["A-1"]
    assert cenario.agente.chamadas[1][0][-1].papel is PapelLLM.FERRAMENTA
    assert resposta.tokens_entrada == 3 + 3 + 5 + 10  # roteador + extração + 2 do agente


async def test_historico_e_itens_citados_entram_no_contexto_do_especialista() -> None:
    conversa = Conversa.nova(LEAD)
    historico = [
        Mensagem.nova(conversa.id, Papel.LEAD, "quero plano"),
        Mensagem.nova(
            conversa.id,
            Papel.AGENTE,
            "Veja o A-1!",
            metadados={"itens_citados": [{"id": "A-1", "resumo": "unidade Centro"}]},
        ),
    ]
    cenario = Cenario(roteador=[{"intencao": "plano", "confianca": 0.9}], extracao=[extracao({})])

    await cenario.grafo.responder(entrada(LEAD, "gostei", historico))

    mensagens = cenario.agente.chamadas[0][0]
    assert [m.conteudo for m in mensagens if m.papel is PapelLLM.USUARIO] == [
        "quero plano",
        "gostei",
    ]
    assert any("A-1: unidade Centro" in m.conteudo for m in mensagens)
    transcricao = cenario.extracao.chamadas_estruturadas[0][0][1].conteudo
    assert "Lead: quero plano\nAtendente: Veja o A-1!\nLead: gostei" in transcricao


async def test_teto_de_passos_forca_texto() -> None:
    cenario = Cenario(
        roteador=[{"intencao": "indefinida", "confianca": 1}],
        agente=[chamar("buscar", "c1"), chamar("buscar", "c2"), texto("pronto")],
    )
    cenario.grafo._max_passos = 2
    resposta = await cenario.grafo.responder(entrada(LEAD))
    assert resposta.texto == "pronto"
    assert [forcar for _, _, forcar in cenario.agente.chamadas] == [False, False, True]
