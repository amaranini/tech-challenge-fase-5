"""Máquina de estados de atendimento (IA × humano) e horário de atendimento — domínio puro."""

from datetime import UTC, datetime, time, timedelta
from itertools import product
from uuid import uuid4

import pytest

from sdr.core.domain.atendimento import (
    TRANSICOES,
    AcaoAtendimento,
    Atendimento,
    EstadoAtendimento,
    HorarioAtendimento,
    MotivoHandoff,
    TipoAcaoAtendimento,
    TransicaoInvalidaError,
    descrever_retorno,
)
from sdr.core.domain.eventos import TipoEvento
from tests.apoio.fakes import FUSO_SP

E = EstadoAtendimento
AGORA = datetime(2026, 10, 5, 10, 0, tzinfo=FUSO_SP)  # segunda, 10h
DEPOIS = AGORA + timedelta(minutes=7)

# Como chegar a cada destino a partir de cada origem (a operação de domínio da transição).
OPERACOES = {
    E.CONFIRMANDO_HANDOFF: lambda a: a.solicitar_handoff(MotivoHandoff.PEDIDO_EXPLICITO, DEPOIS),
    E.AGUARDANDO_HUMANO: lambda a: (
        a.responder_handoff(True, DEPOIS)
        if a.estado is E.CONFIRMANDO_HANDOFF
        else a.responder_retorno_ia(False, DEPOIS)
    ),
    E.ATENDIMENTO_HUMANO: lambda a: a.assumir("Rafael", DEPOIS),
    E.CONFIRMANDO_RETORNO_IA: lambda a: a.solicitar_retorno_ia(DEPOIS),
    E.ATENDIMENTO_IA: lambda a: {
        E.CONFIRMANDO_HANDOFF: lambda: a.responder_handoff(False, DEPOIS),
        E.ATENDIMENTO_HUMANO: lambda: a.devolver(DEPOIS),
        E.CONFIRMANDO_RETORNO_IA: lambda: a.responder_retorno_ia(True, DEPOIS),
    }[a.estado](),
}


def em(estado: EstadoAtendimento) -> Atendimento:
    return Atendimento(uuid4(), estado, AGORA, na_fila_desde=AGORA, responsavel="Rafael")


VALIDAS = [(de, para) for de, destinos in TRANSICOES.items() for para in destinos]
INVALIDAS = [(de, para) for de, para in product(E, E) if para not in TRANSICOES[de]]


def test_tabela_tem_exatamente_as_transicoes_pedidas() -> None:
    assert set(VALIDAS) == {
        (E.ATENDIMENTO_IA, E.CONFIRMANDO_HANDOFF),
        (E.CONFIRMANDO_HANDOFF, E.AGUARDANDO_HUMANO),
        (E.CONFIRMANDO_HANDOFF, E.ATENDIMENTO_IA),
        (E.AGUARDANDO_HUMANO, E.ATENDIMENTO_HUMANO),
        (E.AGUARDANDO_HUMANO, E.CONFIRMANDO_RETORNO_IA),
        (E.ATENDIMENTO_HUMANO, E.ATENDIMENTO_IA),
        (E.CONFIRMANDO_RETORNO_IA, E.ATENDIMENTO_IA),
        (E.CONFIRMANDO_RETORNO_IA, E.AGUARDANDO_HUMANO),
    }


@pytest.mark.parametrize(("de", "para"), VALIDAS, ids=lambda e: e.value)
def test_transicoes_validas(de: EstadoAtendimento, para: EstadoAtendimento) -> None:
    novo, _ = OPERACOES[para](em(de))
    assert novo.estado is para
    assert novo.desde == DEPOIS


OPERACOES_DIRETAS = {
    "solicitar_handoff": lambda a: a.solicitar_handoff(MotivoHandoff.FRUSTRACAO, DEPOIS),
    "responder_handoff": lambda a: a.responder_handoff(True, DEPOIS),
    "assumir": lambda a: a.assumir("Rafael", DEPOIS),
    "devolver": lambda a: a.devolver(DEPOIS),
    "solicitar_retorno_ia": lambda a: a.solicitar_retorno_ia(DEPOIS),
    "responder_retorno_ia": lambda a: a.responder_retorno_ia(True, DEPOIS),
    "mensagem_na_espera": lambda a: a.registrar_mensagem_na_espera(DEPOIS),
}
ORIGEM_VALIDA = {
    "solicitar_handoff": {E.ATENDIMENTO_IA},
    "responder_handoff": {E.CONFIRMANDO_HANDOFF},
    "assumir": {E.AGUARDANDO_HUMANO},
    "devolver": {E.ATENDIMENTO_HUMANO},
    "solicitar_retorno_ia": {E.AGUARDANDO_HUMANO},
    "responder_retorno_ia": {E.CONFIRMANDO_RETORNO_IA},
    "mensagem_na_espera": {E.AGUARDANDO_HUMANO},
}


@pytest.mark.parametrize(
    ("operacao", "estado"),
    [(op, e) for op in OPERACOES_DIRETAS for e in E if e not in ORIGEM_VALIDA[op]],
    ids=lambda v: getattr(v, "value", v),
)
def test_operacao_em_estado_invalido_levanta_erro(operacao: str, estado: EstadoAtendimento) -> None:
    with pytest.raises(TransicaoInvalidaError):
        OPERACOES_DIRETAS[operacao](em(estado))


def test_matriz_de_invalidas_cobre_todo_o_resto() -> None:
    assert len(VALIDAS) + len(INVALIDAS) == len(E) ** 2


def test_handoff_completo_emite_os_eventos() -> None:
    a = Atendimento(uuid4())
    a, e1 = a.solicitar_handoff(MotivoHandoff.PEDIDO_EXPLICITO, AGORA)
    a, e2 = a.responder_handoff(True, AGORA + timedelta(seconds=10))
    a, e3 = a.registrar_mensagem_na_espera(AGORA + timedelta(minutes=3))
    a, e4 = a.assumir("Rafael", AGORA + timedelta(minutes=5))
    a, e5 = a.devolver(AGORA + timedelta(minutes=30))

    tipos = [e.tipo for e in (*e1, *e2, *e3, *e4, *e5)]
    assert tipos == [
        TipoEvento.HANDOFF_SOLICITADO,
        TipoEvento.HANDOFF_CONFIRMADO,
        TipoEvento.MENSAGEM_NA_ESPERA,
        TipoEvento.ATENDIMENTO_HUMANO_INICIADO,
        TipoEvento.ATENDIMENTO_HUMANO_ENCERRADO,
    ]
    assert e3[0].payload["espera_segundos"] == 170
    assert e4[0].payload == {"responsavel": "Rafael", "espera_segundos": 290}
    assert a == Atendimento(a.lead_id, E.ATENDIMENTO_IA, AGORA + timedelta(minutes=30))


def test_retorno_para_ia_cancela_a_fila() -> None:
    a = em(E.AGUARDANDO_HUMANO)
    a, e1 = a.solicitar_retorno_ia(DEPOIS)
    a, e2 = a.responder_retorno_ia(True, DEPOIS)
    assert (a.estado, a.na_fila_desde) == (E.ATENDIMENTO_IA, None)
    assert [e.tipo for e in (*e1, *e2)] == [
        TipoEvento.RETORNO_IA_SOLICITADO,
        TipoEvento.RETORNO_IA_CONFIRMADO,
    ]


def test_desistir_de_voltar_mantem_o_lugar_na_fila() -> None:
    a, _ = em(E.AGUARDANDO_HUMANO).solicitar_retorno_ia(DEPOIS)
    a, eventos = a.responder_retorno_ia(False, DEPOIS)
    assert (a.estado, a.na_fila_desde) == (E.AGUARDANDO_HUMANO, AGORA)
    assert eventos == []


@pytest.mark.parametrize(
    ("estado", "responder", "depois_de_duas"),
    [
        (E.CONFIRMANDO_HANDOFF, "responder_handoff", E.ATENDIMENTO_IA),
        (E.CONFIRMANDO_RETORNO_IA, "responder_retorno_ia", E.AGUARDANDO_HUMANO),
    ],
)
def test_resposta_ambigua_pergunta_de_novo_uma_vez(
    estado: EstadoAtendimento, responder: str, depois_de_duas: EstadoAtendimento
) -> None:
    a = em(estado)
    a, eventos = getattr(a, responder)(None, DEPOIS)
    assert (a.estado, a.respostas_ambiguas, eventos) == (estado, 1, [])

    a, _ = getattr(a, responder)(None, DEPOIS)
    assert a.estado is depois_de_duas
    assert a.respostas_ambiguas == 0


def test_ambigua_duas_vezes_no_handoff_registra_recusa() -> None:
    a, _ = em(E.CONFIRMANDO_HANDOFF).responder_handoff(None, DEPOIS)
    _, eventos = a.responder_handoff(None, DEPOIS)
    assert [(e.tipo, e.payload["motivo"]) for e in eventos] == [
        (TipoEvento.HANDOFF_RECUSADO, "resposta_ambigua")
    ]


def test_acao_do_turno_aplica_a_operacao_do_dominio() -> None:
    acao = AcaoAtendimento(TipoAcaoAtendimento.SOLICITAR_HANDOFF, motivo=MotivoHandoff.NEGOCIACAO)
    novo, eventos = acao.aplicar(Atendimento(uuid4()), AGORA)
    assert (novo.estado, novo.motivo) == (E.CONFIRMANDO_HANDOFF, MotivoHandoff.NEGOCIACAO)
    assert eventos[0].payload == {"motivo": "negociacao"}


# ---------------------------------------------------------------- horário de atendimento

HORARIO = HorarioAtendimento.de_texto("seg-sex", "09:00-12:00,13:00-18:00", "America/Sao_Paulo")


def local(dia: int, hora: int, minuto: int = 0) -> datetime:
    return datetime(2026, 10, dia, hora, minuto, tzinfo=FUSO_SP)  # 05/10 = segunda


@pytest.mark.parametrize(
    ("momento", "aberto"),
    [
        (local(5, 10), True),
        (local(5, 12, 30), False),  # almoço
        (local(5, 18), False),  # fecha às 18h
        (local(5, 8, 59), False),
        (local(10, 10), False),  # sábado
    ],
)
def test_aberto(momento: datetime, aberto: bool) -> None:
    assert HORARIO.aberto(momento) is aberto


@pytest.mark.parametrize(
    ("momento", "abertura"),
    [
        (local(5, 10), local(5, 10)),  # já aberto
        (local(5, 12, 30), local(5, 13)),  # volta do almoço
        (local(5, 19), local(6, 9)),  # amanhã cedo
        (local(9, 19), local(12, 9)),  # sexta à noite → segunda
        (local(10, 15), local(12, 9)),  # sábado → segunda
    ],
)
def test_proxima_abertura(momento: datetime, abertura: datetime) -> None:
    assert HORARIO.proxima_abertura(momento) == abertura


def test_proxima_abertura_funciona_com_instante_em_utc() -> None:
    sexta_19h_utc = local(9, 19).astimezone(UTC)
    assert HORARIO.proxima_abertura(sexta_19h_utc) == local(12, 9)


def test_descricoes() -> None:
    assert HORARIO.descrever() == "de segunda a sexta, das 9h às 12h e das 13h às 18h"
    assert descrever_retorno(local(5, 13), local(5, 12, 30), FUSO_SP) == "hoje às 13h"
    assert descrever_retorno(local(6, 9), local(5, 19), FUSO_SP) == "amanhã (terça) às 9h"
    assert descrever_retorno(local(12, 9), local(9, 19), FUSO_SP) == "segunda (12/10) às 9h"
    assert HorarioAtendimento.de_texto("seg,qua", "08:30-10:00", "UTC").descrever() == (
        "segunda, quarta, das 8h30 às 10h"
    )


def test_horario_invalido() -> None:
    with pytest.raises(ValueError, match="dias e faixas"):
        HorarioAtendimento.de_texto("", "09:00-18:00", "UTC")
    assert HorarioAtendimento.de_texto("sab", "10:00-11:00", "UTC").faixas == (
        (time(10), time(11)),
    )
