"""ConduzirAgendamento com AgendaFake e relógio fake: confirmação, idempotência, conflito.

Relógio fixo: segunda-feira, 05/10/2026, 10h em São Paulo.
"""

from collections.abc import Mapping, Sequence
from datetime import datetime, time, timedelta
from uuid import uuid4

from sdr.core.application.use_cases.conduzir_agendamento import (
    ConduzirAgendamento,
    EntradaAgenda,
    ResultadoAgenda,
)
from sdr.core.domain.agenda import (
    AcaoAgenda,
    Aviso,
    InterpretacaoAgenda,
    NegociacaoAgenda,
    Periodo,
    PreferenciaHorario,
    Responsavel,
    TipoAgendamento,
    TipoDecisao,
)
from sdr.core.domain.eventos import TipoEvento
from tests.apoio.fakes import FUSO_SP, AgendaFake, RelogioFake, grade

AGORA = datetime(2026, 10, 5, 10, 0, tzinfo=FUSO_SP)  # segunda
DIAS = [AGORA.date() + timedelta(days=i) for i in range(5)]  # seg a sex
QUINTA = DIAS[3]

SUL = Responsavel(uuid4(), "Sol", "consultora sul", ("plano:sul",))
NORTE = Responsavel(uuid4(), "Nina", "consultora norte", ("plano:norte",))
TIPOS = {
    "plano": TipoAgendamento("aula", "aula experimental", {"presencial": "na unidade"}),
    "avulso": TipoAgendamento("chamada", "chamada", {"online": "online", "telefone": "telefone"}),
}


class RegraPorUnidade:
    """Vertical fake: unidade "sul" → Sol; senão, Sol e Nina (nessa ordem)."""

    def ordenar(
        self, intencao: str, ficha: Mapping[str, object], responsaveis: Sequence[Responsavel]
    ) -> list[Responsavel]:
        if ficha.get("unidade") == "sul":
            return [r for r in responsaveis if "plano:sul" in r.especialidades]
        return sorted(responsaveis, key=lambda r: r.nome != "Sol")


class Cenario:
    def __init__(self) -> None:
        self.relogio = RelogioFake(AGORA)
        self.agenda = AgendaFake([SUL, NORTE], grade([SUL, NORTE], DIAS))
        self.conduzir = ConduzirAgendamento(
            self.agenda, RegraPorUnidade(), TIPOS, self.relogio, fuso=FUSO_SP
        )
        self.lead_id = uuid4()
        self.negociacao = NegociacaoAgenda()

    async def fala(
        self,
        acao: AcaoAgenda,
        intencao: str = "plano",
        ficha: Mapping[str, object] | None = None,
        **kwargs: object,
    ) -> ResultadoAgenda:
        resultado = await self.conduzir.executar(
            EntradaAgenda(
                lead_id=self.lead_id,
                intencao=intencao,
                ficha=ficha or {"unidade": "sul"},
                negociacao=self.negociacao,
                interpretacao=InterpretacaoAgenda(acao, **kwargs),  # type: ignore[arg-type]
                ativo=await self.conduzir.agendamento_ativo(self.lead_id),
                itens=("A-1",),
            )
        )
        self.negociacao = resultado.decisao.negociacao
        return resultado


QUINTA_A_TARDE = PreferenciaHorario(dias_semana=(3,), periodo=Periodo.TARDE)


async def test_fluxo_oferece_propoe_confirma_e_reserva() -> None:
    c = Cenario()

    oferta = await c.fala(AcaoAgenda.NENHUMA)
    assert oferta.decisao.tipo is TipoDecisao.OFERECER
    assert len(oferta.decisao.opcoes) == 3
    assert {s.responsavel_id for s in oferta.decisao.opcoes} == {SUL.id}  # regra da vertical

    proposta = await c.fala(AcaoAgenda.PREFERENCIA, preferencia=QUINTA_A_TARDE)
    assert proposta.decisao.tipo is TipoDecisao.PEDIR_CONFIRMACAO
    assert c.agenda.chamadas == []  # nada reservado antes da confirmação
    assert proposta.eventos == ()

    feito = await c.fala(AcaoAgenda.CONFIRMAR)
    assert feito.decisao.tipo is TipoDecisao.AGENDADO
    assert feito.agendamento is not None
    assert feito.agendamento.inicio == datetime.combine(QUINTA, time(14), FUSO_SP)
    assert feito.agendamento.responsavel == SUL
    assert feito.agendamento.modalidade == "presencial"
    assert feito.agendamento.itens == ("A-1",)
    assert [e.tipo for e in feito.eventos] == [TipoEvento.AGENDAMENTO_CRIADO]
    assert feito.eventos[0].payload["responsavel_titulo"] == "consultora sul"
    assert c.negociacao == NegociacaoAgenda()

    depois = await c.fala(AcaoAgenda.NENHUMA)
    assert depois.decisao.tipo is TipoDecisao.MANTER
    assert depois.agendamento == feito.agendamento


async def test_reserva_idempotente_ao_reprocessar_o_mesmo_sim() -> None:
    c = Cenario()
    await c.fala(AcaoAgenda.PREFERENCIA, preferencia=QUINTA_A_TARDE)
    pendente = c.negociacao

    primeiro = await c.fala(AcaoAgenda.CONFIRMAR)
    # Turno reprocessado (chegou mensagem antes do envio): mesma negociação de antes,
    # mesmo agendamento — sem reservar duas vezes.
    c.negociacao = pendente
    resultado = await c.conduzir.executar(
        EntradaAgenda(
            c.lead_id,
            "plano",
            {"unidade": "sul"},
            pendente,
            InterpretacaoAgenda(AcaoAgenda.CONFIRMAR),
            ativo=None,  # ex.: ativo lido antes do 1º turno gravar
        )
    )
    assert resultado.decisao.tipo is TipoDecisao.AGENDADO
    assert resultado.agendamento == primeiro.agendamento
    assert len(c.agenda.agendamentos) == 1


async def test_slot_tomado_entre_proposta_e_confirmacao_oferece_alternativas() -> None:
    c = Cenario()
    proposta = await c.fala(AcaoAgenda.PREFERENCIA, preferencia=QUINTA_A_TARDE)
    assert proposta.decisao.proposta is not None
    slot = proposta.decisao.proposta.slot
    assert slot is not None
    c.agenda.ocupar_por_fora(slot.id)  # outro lead confirmou antes

    resultado = await c.fala(AcaoAgenda.CONFIRMAR)

    assert resultado.decisao.tipo is TipoDecisao.OFERECER
    assert resultado.decisao.aviso is Aviso.SLOT_TOMADO
    assert slot not in resultado.decisao.opcoes
    assert resultado.eventos == ()
    assert c.agenda.agendamentos == {}


async def test_corrida_entre_dois_leads_pelo_mesmo_slot() -> None:
    a, b = Cenario(), Cenario()
    b.agenda = a.agenda  # mesma agenda
    b.conduzir = a.conduzir
    for lead in (a, b):
        await lead.fala(AcaoAgenda.PREFERENCIA, preferencia=QUINTA_A_TARDE)

    primeiro = await a.fala(AcaoAgenda.CONFIRMAR)
    segundo = await b.fala(AcaoAgenda.CONFIRMAR)

    assert primeiro.decisao.tipo is TipoDecisao.AGENDADO
    assert segundo.decisao.aviso is Aviso.SLOT_TOMADO
    assert len(a.agenda.agendamentos) == 1


async def test_respeita_antecedencia_minima() -> None:
    c = Cenario()
    oferta = await c.fala(AcaoAgenda.PEDIR_HORARIOS)
    assert min(s.inicio for s in oferta.decisao.opcoes) >= AGORA + timedelta(hours=2)
    hoje = await c.fala(AcaoAgenda.PREFERENCIA, preferencia=PreferenciaHorario(hora=11))
    assert hoje.decisao.proposta is not None
    assert hoje.decisao.proposta.slot is not None
    assert hoje.decisao.proposta.slot.inicio.date() != AGORA.date()  # 11h de hoje já não dá


async def test_mesmo_horario_em_dois_responsaveis_prefere_o_primeiro_da_regra() -> None:
    c = Cenario()
    oferta = await c.fala(AcaoAgenda.PEDIR_HORARIOS, ficha={"unidade": "centro"})
    assert {s.responsavel_id for s in oferta.decisao.opcoes} == {SUL.id}
    c.agenda.slots = {k: s for k, s in c.agenda.slots.items() if s.responsavel_id != SUL.id}
    c.negociacao = NegociacaoAgenda()
    oferta = await c.fala(AcaoAgenda.PEDIR_HORARIOS, ficha={"unidade": "centro"})
    assert {s.responsavel_id for s in oferta.decisao.opcoes} == {NORTE.id}  # sobra a Nina


async def test_remarca_e_cancela_com_confirmacao() -> None:
    c = Cenario()
    await c.fala(AcaoAgenda.PREFERENCIA, preferencia=QUINTA_A_TARDE)
    criado = await c.fala(AcaoAgenda.CONFIRMAR)
    assert criado.agendamento is not None

    sexta = PreferenciaHorario(dias_semana=(4,), periodo=Periodo.MANHA)
    proposta = await c.fala(AcaoAgenda.REMARCAR, preferencia=sexta)
    assert proposta.decisao.tipo is TipoDecisao.PEDIR_CONFIRMACAO
    assert c.agenda.chamadas == ["reservar"]

    remarcado = await c.fala(AcaoAgenda.CONFIRMAR)
    assert remarcado.decisao.tipo is TipoDecisao.REMARCADO
    assert remarcado.agendamento is not None
    assert remarcado.agendamento.id == criado.agendamento.id
    assert remarcado.agendamento.inicio == datetime.combine(DIAS[4], time(9), FUSO_SP)
    assert remarcado.eventos[0].tipo is TipoEvento.AGENDAMENTO_REMARCADO
    assert remarcado.eventos[0].payload["inicio_anterior"] == criado.agendamento.inicio.isoformat()
    assert criado.agendamento.slot_id not in c.agenda.ocupados  # liberou o antigo

    pedido = await c.fala(AcaoAgenda.CANCELAR)
    assert pedido.decisao.tipo is TipoDecisao.PEDIR_CONFIRMACAO
    cancelado = await c.fala(AcaoAgenda.CONFIRMAR)
    assert cancelado.decisao.tipo is TipoDecisao.CANCELADO
    assert cancelado.eventos[0].tipo is TipoEvento.AGENDAMENTO_CANCELADO
    assert await c.conduzir.agendamento_ativo(c.lead_id) is None


async def test_duas_modalidades_reserva_com_a_escolhida() -> None:
    c = Cenario()
    amanha_cedo = PreferenciaHorario(daqui_a_dias=1, periodo=Periodo.MANHA)
    pergunta = await c.fala(AcaoAgenda.PREFERENCIA, intencao="avulso", preferencia=amanha_cedo)
    assert pergunta.decisao.aviso is Aviso.FALTA_MODALIDADE

    feito = await c.fala(AcaoAgenda.CONFIRMAR, intencao="avulso", modalidade="online")
    assert feito.decisao.tipo is TipoDecisao.AGENDADO
    assert feito.agendamento is not None
    assert feito.agendamento.tipo == "chamada"
    assert feito.agendamento.modalidade == "online"


async def test_ninguem_apto_fica_sem_disponibilidade() -> None:
    c = Cenario()
    c.agenda.responsaveis = {}
    resultado = await c.fala(AcaoAgenda.PEDIR_HORARIOS, ficha={"unidade": "sul"})
    assert resultado.decisao.tipo is TipoDecisao.SEM_DISPONIBILIDADE
