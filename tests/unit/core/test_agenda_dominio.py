"""Domínio da agenda: datas relativas no fuso da operação, sugestões e a negociação.

Relógio fixo: segunda-feira, 05/10/2026, 10h em São Paulo.
"""

from datetime import UTC, date, datetime, time, timedelta
from uuid import uuid4

import pytest

from sdr.core.domain.agenda import (
    AcaoAgenda,
    Agendamento,
    Aviso,
    Decisao,
    InterpretacaoAgenda,
    NegociacaoAgenda,
    Operacao,
    Periodo,
    PreferenciaHorario,
    Proposta,
    Responsavel,
    Slot,
    StatusAgendamento,
    TipoAgendamento,
    TipoDecisao,
    decidir,
    descrever_horario,
    sugerir,
)
from tests.apoio.fakes import FUSO_SP, grade

AGORA = datetime(2026, 10, 5, 10, 0, tzinfo=FUSO_SP)  # segunda
HOJE = AGORA.date()
SEGUNDA, TERCA, QUARTA, QUINTA, SEXTA = (HOJE + timedelta(days=i) for i in range(5))
PROXIMA_SEGUNDA = HOJE + timedelta(days=7)
PROXIMA_QUINTA = QUINTA + timedelta(days=7)

ANA = Responsavel(uuid4(), "Ana", "consultora")
PRESENCIAL = TipoAgendamento("encontro", "encontro", {"presencial": "presencial"})
DUAS_MODALIDADES = TipoAgendamento(
    "reuniao", "reunião", {"online": "online", "escritorio": "no escritório"}
)
DIAS_UTEIS = [SEGUNDA, TERCA, QUARTA, QUINTA, SEXTA, PROXIMA_SEGUNDA, PROXIMA_QUINTA]
LIVRES = [s for s in grade([ANA], DIAS_UTEIS) if s.inicio > AGORA + timedelta(hours=2)]


def local(slot: Slot) -> datetime:
    return slot.inicio.astimezone(FUSO_SP)


def interp(acao: AcaoAgenda, **kwargs: object) -> InterpretacaoAgenda:
    return InterpretacaoAgenda(acao, **kwargs)  # type: ignore[arg-type]


def passo(
    interpretacao: InterpretacaoAgenda,
    negociacao: NegociacaoAgenda | None = None,
    ativo: Agendamento | None = None,
    livres: list[Slot] | None = None,
    tipo: TipoAgendamento = PRESENCIAL,
) -> Decisao:
    return decidir(
        interpretacao,
        negociacao or NegociacaoAgenda(),
        ativo,
        LIVRES if livres is None else livres,
        tipo,
        hoje=HOJE,
        fuso=FUSO_SP,
    )


def agendamento_em(slot: Slot) -> Agendamento:
    return Agendamento(
        uuid4(), uuid4(), ANA, slot.id, slot.inicio, slot.fim, "encontro", "presencial",
        StatusAgendamento.ATIVO, AGORA,
    )  # fmt: skip


# ---------------------------------------------------------------- datas relativas


@pytest.mark.parametrize(
    ("preferencia", "datas_esperadas"),
    [
        (PreferenciaHorario(dias_semana=(3,)), {QUINTA, PROXIMA_QUINTA}),  # "quinta"
        (PreferenciaHorario(daqui_a_dias=1), {TERCA}),  # "amanhã"
        (PreferenciaHorario(daqui_a_dias=0), {SEGUNDA}),  # "hoje"
        (PreferenciaHorario(dias_semana=(0,)), {SEGUNDA, PROXIMA_SEGUNDA}),  # "segunda" = hoje
        (PreferenciaHorario(data=date(2026, 10, 15)), {date(2026, 10, 15)}),  # "dia 15"
        (PreferenciaHorario(periodo=Periodo.TARDE), None),  # qualquer dia
    ],
)
def test_resolve_datas_relativas_a_partir_de_hoje(
    preferencia: PreferenciaHorario, datas_esperadas: set[date] | None
) -> None:
    assert preferencia.datas(HOJE) == datas_esperadas


def test_quinta_a_tarde_atende_so_quinta_entre_12h_e_18h() -> None:
    pref = PreferenciaHorario(dias_semana=(3,), periodo=Periodo.TARDE)
    atendem = [local(s) for s in LIVRES if pref.atende(local(s), HOJE)]
    assert atendem
    assert {d.date() for d in atendem} == {QUINTA, PROXIMA_QUINTA}
    assert all(time(12) <= d.time() < time(18) for d in atendem)


def test_amanha_cedo_e_depois_das_18h() -> None:
    cedo = PreferenciaHorario(daqui_a_dias=1, periodo=Periodo.MANHA, antes_hora=10)
    assert [local(s).hour for s in LIVRES if cedo.atende(local(s), HOJE)] == [9]
    noite = PreferenciaHorario(apos_hora=18)
    assert {local(s).hour for s in LIVRES if noite.atende(local(s), HOJE)} == {18, 19}


def test_horario_resolvido_no_fuso_da_operacao_mesmo_com_slot_em_utc() -> None:
    slot = LIVRES[0]
    em_utc = Slot(slot.id, slot.responsavel_id, slot.inicio.astimezone(UTC), slot.fim)
    pref = PreferenciaHorario(hora=local(slot).hour)
    assert pref.atende(em_utc.inicio.astimezone(FUSO_SP), HOJE)


def test_descreve_horario_em_portugues() -> None:
    quinta_14h = datetime.combine(QUINTA, time(14), FUSO_SP)
    assert descrever_horario(quinta_14h, FUSO_SP, HOJE) == "quinta (08/10) às 14h"
    amanha = datetime.combine(TERCA, time(9, 30), FUSO_SP)
    assert descrever_horario(amanha, FUSO_SP, HOJE) == "amanhã, terça (06/10) às 9h30"


def test_sugere_tres_horarios_variados() -> None:
    opcoes = sugerir(LIVRES, FUSO_SP, 3)
    assert len(opcoes) == 3
    assert opcoes[0] == min(LIVRES, key=lambda s: s.inicio)  # o mais cedo primeiro
    assert len({local(s).date() for s in opcoes}) == 3  # dias diferentes
    assert len({local(s).hour < 12 for s in opcoes}) == 2  # manhã e tarde/noite


# ---------------------------------------------------------------- negociação


def test_sem_proposta_oferece_horarios() -> None:
    decisao = passo(interp(AcaoAgenda.NENHUMA))
    assert decisao.tipo is TipoDecisao.OFERECER
    assert len(decisao.opcoes) == 3
    assert decisao.negociacao.ofertados == decisao.opcoes


def test_preferencia_vira_proposta_e_nunca_executa_sem_confirmacao() -> None:
    pref = PreferenciaHorario(dias_semana=(3,), periodo=Periodo.TARDE)
    decisao = passo(interp(AcaoAgenda.PREFERENCIA, preferencia=pref))

    assert decisao.tipo is TipoDecisao.PEDIR_CONFIRMACAO
    proposta = decisao.proposta
    assert proposta is not None
    assert proposta.operacao is Operacao.RESERVAR
    assert proposta.slot is not None
    assert local(proposta.slot) == datetime.combine(QUINTA, time(14), FUSO_SP)  # 1º à tarde
    assert proposta.modalidade == "presencial"  # modalidade única já vem preenchida


def test_confirmacao_explicita_executa_a_proposta() -> None:
    proposta = Proposta(Operacao.RESERVAR, LIVRES[5], "presencial")
    decisao = passo(interp(AcaoAgenda.CONFIRMAR), NegociacaoAgenda(proposta=proposta))
    assert decisao.tipo is TipoDecisao.EXECUTAR
    assert decisao.proposta == proposta
    assert decisao.negociacao == NegociacaoAgenda()


def test_assunto_paralelo_mantem_a_proposta_pendente() -> None:
    proposta = Proposta(Operacao.RESERVAR, LIVRES[5], "presencial")
    decisao = passo(interp(AcaoAgenda.NENHUMA), NegociacaoAgenda(proposta=proposta))
    assert decisao.tipo is TipoDecisao.PEDIR_CONFIRMACAO
    assert decisao.negociacao.proposta == proposta


def test_preferencia_escolhe_opcao_oferecida_pelo_numero() -> None:
    ofertados = sugerir(LIVRES, FUSO_SP, 3)
    decisao = passo(interp(AcaoAgenda.PREFERENCIA, opcao=2), NegociacaoAgenda(ofertados))
    assert decisao.proposta is not None
    assert decisao.proposta.slot == ofertados[1]


def test_sem_horario_na_preferencia_oferece_alternativas_proximas() -> None:
    pref = PreferenciaHorario(dias_semana=(3,), apos_hora=21)  # quinta depois das 21h
    decisao = passo(interp(AcaoAgenda.PREFERENCIA, preferencia=pref))
    assert decisao.tipo is TipoDecisao.OFERECER
    assert decisao.aviso is Aviso.SEM_HORARIO_NA_PREFERENCIA
    assert {local(s).date() for s in decisao.opcoes} <= {QUINTA, PROXIMA_QUINTA}  # mesmo dia


def test_duas_modalidades_pergunta_antes_de_confirmar() -> None:
    pref = PreferenciaHorario(daqui_a_dias=1, periodo=Periodo.MANHA)
    decisao = passo(interp(AcaoAgenda.PREFERENCIA, preferencia=pref), tipo=DUAS_MODALIDADES)
    assert decisao.tipo is TipoDecisao.PEDIR_CONFIRMACAO
    assert decisao.aviso is Aviso.FALTA_MODALIDADE

    # "sim" sem dizer a modalidade: ainda não executa
    sim = passo(interp(AcaoAgenda.CONFIRMAR), decisao.negociacao, tipo=DUAS_MODALIDADES)
    assert sim.tipo is TipoDecisao.PEDIR_CONFIRMACAO
    assert sim.aviso is Aviso.FALTA_MODALIDADE

    # só respondeu "online": pede a confirmação completa
    online = passo(
        interp(AcaoAgenda.NENHUMA, modalidade="online"), decisao.negociacao, tipo=DUAS_MODALIDADES
    )
    assert online.tipo is TipoDecisao.PEDIR_CONFIRMACAO
    assert online.aviso is None
    assert online.proposta is not None
    assert online.proposta.modalidade == "online"

    confirmado = passo(interp(AcaoAgenda.CONFIRMAR), online.negociacao, tipo=DUAS_MODALIDADES)
    assert confirmado.tipo is TipoDecisao.EXECUTAR


def test_modalidade_invalida_e_ignorada() -> None:
    pref = PreferenciaHorario(daqui_a_dias=1)
    decisao = passo(
        interp(AcaoAgenda.PREFERENCIA, preferencia=pref, modalidade="teletransporte"),
        tipo=DUAS_MODALIDADES,
    )
    assert decisao.aviso is Aviso.FALTA_MODALIDADE


def test_recusar_proposta_oferece_outros_horarios() -> None:
    proposta = Proposta(Operacao.RESERVAR, LIVRES[0], "presencial")
    decisao = passo(interp(AcaoAgenda.RECUSAR), NegociacaoAgenda(proposta=proposta))
    assert decisao.tipo is TipoDecisao.OFERECER
    assert decisao.aviso is Aviso.PROPOSTA_RECUSADA
    assert LIVRES[0] not in decisao.opcoes


def test_recusar_agendar_nao_insiste_depois() -> None:
    decisao = passo(interp(AcaoAgenda.RECUSAR))
    assert decisao.tipo is TipoDecisao.NAO_INSISTIR
    depois = passo(interp(AcaoAgenda.NENHUMA), decisao.negociacao)
    assert depois.tipo is TipoDecisao.NAO_INSISTIR
    mudou_de_ideia = passo(interp(AcaoAgenda.PEDIR_HORARIOS), decisao.negociacao)
    assert mudou_de_ideia.tipo is TipoDecisao.OFERECER


def test_lembra_opcoes_ainda_livres_quando_lead_fala_de_outra_coisa() -> None:
    ofertados = sugerir(LIVRES, FUSO_SP, 3)
    livres = [s for s in LIVRES if s != ofertados[0]]  # a 1ª foi tomada nesse meio-tempo
    decisao = passo(interp(AcaoAgenda.NENHUMA), NegociacaoAgenda(ofertados), livres=livres)
    assert decisao.aviso is Aviso.LEMBRETE
    assert decisao.opcoes[:2] == ofertados[1:]
    assert ofertados[0] not in decisao.opcoes


def test_remarcar_propoe_novo_horario_do_agendamento_ativo() -> None:
    ativo = agendamento_em(LIVRES[0])
    pref = PreferenciaHorario(dias_semana=(4,), periodo=Periodo.MANHA)  # sexta de manhã
    decisao = passo(interp(AcaoAgenda.REMARCAR, preferencia=pref), ativo=ativo)
    assert decisao.tipo is TipoDecisao.PEDIR_CONFIRMACAO
    assert decisao.proposta is not None
    assert decisao.proposta.operacao is Operacao.REMARCAR
    assert decisao.proposta.agendamento_id == ativo.id
    assert decisao.proposta.slot is not None
    assert local(decisao.proposta.slot).date() == SEXTA


def test_cancelar_pede_confirmacao_e_recusa_mantem() -> None:
    ativo = agendamento_em(LIVRES[0])
    decisao = passo(interp(AcaoAgenda.CANCELAR), ativo=ativo)
    assert decisao.tipo is TipoDecisao.PEDIR_CONFIRMACAO
    assert decisao.proposta == Proposta(Operacao.CANCELAR, agendamento_id=ativo.id)

    assert passo(interp(AcaoAgenda.RECUSAR), decisao.negociacao, ativo).tipo is TipoDecisao.MANTER
    sim = passo(interp(AcaoAgenda.CONFIRMAR), decisao.negociacao, ativo)
    assert sim.tipo is TipoDecisao.EXECUTAR


def test_cancelar_sem_agendamento_avisa() -> None:
    decisao = passo(interp(AcaoAgenda.CANCELAR))
    assert decisao.tipo is TipoDecisao.MANTER
    assert decisao.aviso is Aviso.SEM_AGENDAMENTO


def test_com_agendamento_e_outro_assunto_mantem() -> None:
    assert passo(interp(AcaoAgenda.NENHUMA), ativo=agendamento_em(LIVRES[0])).tipo is (
        TipoDecisao.MANTER
    )


def test_proposta_ja_executada_nao_executa_de_novo() -> None:
    ativo = agendamento_em(LIVRES[0])
    proposta = Proposta(Operacao.RESERVAR, LIVRES[0], "presencial")
    decisao = passo(interp(AcaoAgenda.CONFIRMAR), NegociacaoAgenda(proposta=proposta), ativo)
    assert decisao.tipo is TipoDecisao.MANTER


def test_sem_slots_livres() -> None:
    decisao = passo(interp(AcaoAgenda.PEDIR_HORARIOS), livres=[])
    assert decisao.tipo is TipoDecisao.SEM_DISPONIBILIDADE


def test_opcao_numerada_incompativel_com_a_preferencia_e_ignorada() -> None:
    ofertados = sugerir(LIVRES, FUSO_SP, 3)  # a 1ª não é sexta
    sexta = PreferenciaHorario(dias_semana=(4,), periodo=Periodo.MANHA)
    assert local(ofertados[0]).date() != SEXTA
    decisao = passo(
        interp(AcaoAgenda.PREFERENCIA, preferencia=sexta, opcao=1), NegociacaoAgenda(ofertados)
    )
    assert decisao.proposta is not None
    assert decisao.proposta.slot is not None
    assert local(decisao.proposta.slot).date() == SEXTA


def test_sim_pode_cancelar_com_cancelamento_pendente_confirma() -> None:
    ativo = agendamento_em(LIVRES[0])
    pedido = passo(interp(AcaoAgenda.CANCELAR), ativo=ativo)
    sim_cancela = passo(interp(AcaoAgenda.CANCELAR), pedido.negociacao, ativo)
    assert sim_cancela.tipo is TipoDecisao.EXECUTAR
    assert sim_cancela.proposta is not None
    assert sim_cancela.proposta.operacao is Operacao.CANCELAR
    assert sim_cancela.proposta.agendamento_id == ativo.id
