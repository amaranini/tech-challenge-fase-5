"""Nó de agendamento no grafo genérico (vertical fake, LLMs roteirizados, AgendaFake)."""

from dataclasses import replace
from datetime import datetime, timedelta
from uuid import uuid4

from sdr.core.adapters.outbound.agent.agenda import ler_interpretacao
from sdr.core.adapters.outbound.agent.agente_qualificador import (
    AgenteQualificador,
    ConfigQualificacao,
    LLMsPorNo,
    sem_repeticao,
)
from sdr.core.application.ports.agente import EntradaAgente
from sdr.core.application.use_cases.conduzir_agendamento import ConduzirAgendamento
from sdr.core.domain.agenda import (
    AcaoAgenda,
    NegociacaoAgenda,
    Operacao,
    Periodo,
    PreferenciaHorario,
    Proposta,
    Responsavel,
    TipoAgendamento,
)
from sdr.core.domain.agente import Persona
from sdr.core.domain.conversa import Canal, Lead
from sdr.core.domain.eventos import TipoEvento
from sdr.core.domain.qualificacao import Qualificacao
from tests.apoio.fakes import FUSO_SP, AgendaFake, LLMRoteirizado, RelogioFake, grade, texto
from tests.apoio.vertical_fake import INTENCOES, RegrasFake

AGORA = datetime(2026, 10, 5, 10, 0, tzinfo=FUSO_SP)  # segunda
DIAS = [AGORA.date() + timedelta(days=i) for i in range(5)]
SOL = Responsavel(uuid4(), "Sol", "consultora")
AULA = TipoAgendamento("aula", "aula experimental", {"presencial": "na unidade"})
CHAMADA = TipoAgendamento("chamada", "chamada", {"online": "online", "telefone": "telefone"})
PERSONA = Persona(nome="Bia", versao_prompt="v1", prompt_sistema="PERSONA_BIA")
FICHA_COMPLETA = {"unidade": "Centro", "orcamento": 150, "horario": "noite"}  # 75 pts


class TodosAtendem:
    def ordenar(self, intencao, ficha, responsaveis):  # type: ignore[no-untyped-def]
        return list(responsaveis)


def nada_extraido() -> dict[str, object]:
    return {"campos": {}, "campos_corrigidos": [], "campos_removidos": []}


def interpretacao(acao: str, **extra: object) -> dict[str, object]:
    return {"acao": acao, "opcao": None, "preferencia": {}, "modalidade": None, **extra}


class Cenario:
    def __init__(
        self, interpretacoes: list[dict[str, object]], agenda: AgendaFake | None = None
    ) -> None:
        self.roteador = LLMRoteirizado(
            estruturadas=[{"intencao": "plano", "confianca": 0.9}] * len(interpretacoes or [{}])
        )
        self.extracao = LLMRoteirizado(estruturadas=[nada_extraido(), *interpretacoes])
        self.agente = LLMRoteirizado(texto("ok"))
        self.agenda = agenda or AgendaFake([SOL], grade([SOL], DIAS))
        relogio = RelogioFake(AGORA)
        self.conduzir = ConduzirAgendamento(
            self.agenda, TodosAtendem(), {"plano": AULA, "avulso": CHAMADA}, relogio, fuso=FUSO_SP
        )
        self.grafo = AgenteQualificador(
            LLMsPorNo(self.roteador, self.extracao, self.agente),
            PERSONA,
            ConfigQualificacao(INTENCOES, RegrasFake(), "PROMPT_DESCOBERTA"),
            ferramentas=[],
            fuso=FUSO_SP,
            agenda=self.conduzir,
            relogio=relogio,
        )

    def bloco(self) -> str:
        mensagens = self.agente.chamadas[-1][0]
        return next(m.conteudo for m in mensagens if m.conteudo.startswith("[Agenda"))


def lead(ficha: dict[str, object], negociacao: NegociacaoAgenda | None = None) -> Lead:
    base = Lead.novo(Canal.WEB, "lead-agenda")
    return replace(
        base,
        qualificacao=Qualificacao(base.id, intencao_atual="plano", fichas={"plano": ficha}),
        agenda=negociacao or NegociacaoAgenda(),
    )


async def test_lead_qualificado_vai_para_agenda_e_recebe_horarios_reais() -> None:
    c = Cenario([interpretacao("nenhuma")])

    resposta = await c.grafo.responder(EntradaAgente(lead(FICHA_COMPLETA), [], "show"))

    _, schema, nome = c.extracao.chamadas_estruturadas[1]
    assert nome == "interpretacao_agenda"
    assert "segunda, 05/10/2026 10:00" in c.extracao.chamadas_estruturadas[1][0][0].conteudo
    assert schema["properties"]["acao"]["enum"] == [a.value for a in AcaoAgenda]  # type: ignore[index]
    assert resposta.metadados["no_resposta"] == "responder_agenda"
    assert resposta.metadados["agenda"]["decisao"] == "oferecer"  # type: ignore[index]
    assert resposta.agenda is not None
    assert len(resposta.agenda.ofertados) == 3
    bloco = c.bloco()
    assert "Convide o lead para a aula experimental e ofereça estes horários" in bloco
    assert "1. hoje, segunda (05/10) às 14h com Sol (consultora)" in bloco
    assert c.agenda.chamadas == []


async def test_lead_nao_qualificado_segue_no_especialista() -> None:
    c = Cenario([])

    resposta = await c.grafo.responder(EntradaAgente(lead({"unidade": "Centro"}), [], "oi"))

    assert resposta.metadados["no_resposta"] == "especialista"
    assert len(c.extracao.chamadas_estruturadas) == 1  # só a extração da ficha
    assert "agenda" not in resposta.metadados


async def test_quinta_a_tarde_pede_confirmacao_e_sim_reserva() -> None:
    quinta_tarde = {"dias_semana": ["quinta"], "periodo": "tarde"}
    c = Cenario([interpretacao("preferencia", preferencia=quinta_tarde)])

    proposta = await c.grafo.responder(
        EntradaAgente(lead(FICHA_COMPLETA), [], "pode ser quinta à tarde?")
    )
    assert "Peça confirmação EXPLÍCITA para agendar" in c.bloco()
    assert "quinta (08/10) às 14h com Sol (consultora), na unidade" in c.bloco()
    assert c.agenda.chamadas == []
    assert proposta.agenda is not None
    assert proposta.agenda.proposta is not None

    c2 = Cenario([interpretacao("confirmar")], agenda=c.agenda)
    feito = await c2.grafo.responder(
        EntradaAgente(lead(FICHA_COMPLETA, proposta.agenda), [], "sim")
    )

    assert [e.tipo for e in feito.eventos if e.tipo.name.startswith("AGENDAMENTO")] == [
        TipoEvento.AGENDAMENTO_CRIADO
    ]
    assert "AGENDADO com sucesso" in c2.bloco()
    assert feito.agenda == NegociacaoAgenda()


async def test_lead_que_nao_quer_agendar_volta_ao_especialista_sem_insistir() -> None:
    c = Cenario([interpretacao("recusar")])

    resposta = await c.grafo.responder(
        EntradaAgente(lead(FICHA_COMPLETA), [], "agora não, só pesquisando")
    )

    assert resposta.metadados["no_resposta"] == "especialista"
    assert resposta.agenda == NegociacaoAgenda(recusou=True)
    mensagens = c.agente.chamadas[0][0]
    bloco = next(m.conteudo for m in mensagens if "Estado da qualificação" in m.conteudo)
    assert "não quer agendar agora: NÃO insista" in bloco


async def test_negociacao_em_curso_vai_para_agenda_mesmo_sem_qualificar() -> None:
    slot = grade([SOL], DIAS[3:4], horas=(14,))[0]
    c = Cenario([interpretacao("nenhuma")])
    c.agenda.slots[slot.id] = slot
    negociacao = NegociacaoAgenda(proposta=Proposta(Operacao.RESERVAR, slot, "presencial"))

    resposta = await c.grafo.responder(EntradaAgente(lead({}, negociacao), [], "hum"))

    assert resposta.metadados["no_resposta"] == "responder_agenda"
    assert "Peça confirmação EXPLÍCITA" in c.bloco()


def test_ler_interpretacao_estrutura_e_descarta_invalidos() -> None:
    dados = {
        "acao": "preferencia",
        "opcao": 2,
        "preferencia": {
            "dias_semana": ["quinta", "domingão"],
            "data": "2026-10-15",
            "daqui_a_dias": None,
            "periodo": "tarde",
            "apos_hora": 18,
            "antes_hora": 99,
            "hora": True,
        },
        "modalidade": "online",
    }
    lida = ler_interpretacao(dados, CHAMADA)
    assert lida.acao is AcaoAgenda.PREFERENCIA
    assert lida.opcao == 2
    assert lida.modalidade == "online"
    assert lida.preferencia == PreferenciaHorario(
        dias_semana=(3,),
        data=datetime(2026, 10, 15).date(),
        periodo=Periodo.TARDE,
        apos_hora=18,
    )
    assert ler_interpretacao({"acao": "dançar", "modalidade": "online"}, AULA).acao is (
        AcaoAgenda.NENHUMA
    )
    assert ler_interpretacao({"acao": "confirmar", "modalidade": "online"}, AULA).modalidade is None


def test_resposta_duplicada_na_mesma_geracao_e_colapsada() -> None:
    bloco = "Posso agendar?\n\n- Amanhã às 9h\n- Sexta às 12h"
    assert sem_repeticao(f"{bloco}\n{bloco.replace('9h', '9h  ')}") == bloco
    assert sem_repeticao("Oi!\nOi!") == "Oi!"
    assert sem_repeticao("Oi!\nTudo bem?") == "Oi!\nTudo bem?"


def test_dias_da_semana_com_ou_sem_acento() -> None:
    for grafia in ("terca", "terça", "Terça"):
        lida = ler_interpretacao(
            {"acao": "preferencia", "preferencia": {"dias_semana": [grafia, "sabado"]}}, AULA
        )
        assert lida.preferencia.dias_semana == (1, 5)


async def test_pedido_de_agendar_abre_a_agenda_mesmo_sem_qualificar() -> None:
    c = Cenario([interpretacao("confirmar")])
    c.roteador = LLMRoteirizado(
        estruturadas=[{"intencao": "plano", "confianca": 0.9, "quer_agendar": True}]
    )
    c.grafo = AgenteQualificador(
        LLMsPorNo(c.roteador, c.extracao, c.agente),
        PERSONA,
        ConfigQualificacao(INTENCOES, RegrasFake(), "PROMPT_DESCOBERTA"),
        ferramentas=[],
        fuso=FUSO_SP,
        agenda=c.conduzir,
        relogio=RelogioFake(AGORA),
    )

    resposta = await c.grafo.responder(EntradaAgente(lead({"unidade": "Centro"}), [], "sim"))

    assert resposta.metadados["no_resposta"] == "responder_agenda"
    assert resposta.metadados["agenda"]["decisao"] == "oferecer"  # type: ignore[index]
    assert resposta.agenda is not None
    assert len(resposta.agenda.ofertados) == 3


async def test_limites_do_assistente_em_toda_resposta_com_o_proximo_passo_real() -> None:
    c = Cenario([interpretacao("nenhuma")])
    await c.grafo.responder(EntradaAgente(lead(FICHA_COMPLETA), [], "tem fotos?"))

    limites = next(
        m.conteudo for m in c.agente.chamadas[0][0] if m.conteudo.startswith("# O que você")
    )
    assert "NÃO consegue: enviar fotos" in limites
    assert "nunca diga que tem" in limites
    assert "próximo passo que existe de verdade: marcar aula experimental" in limites
