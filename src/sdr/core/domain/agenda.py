"""Agenda genérica: responsáveis, slots, agendamentos e a negociação de horário com o lead.

O core não sabe O QUE se agenda (o tipo vem da vertical: `TipoAgendamento`) nem QUEM atende
(`Responsavel`, com título e especialidades definidos pela vertical). Aqui ficam só regras
puras e testáveis com relógio fake:
- resolver uma preferência já estruturada ("quinta à tarde" → dias_semana + período) para
  datas e horários reais no fuso da operação;
- escolher 2–3 sugestões variadas (dias e períodos diferentes);
- decidir o próximo passo da negociação — oferecer, pedir confirmação, executar, manter —
  com confirmação EXPLÍCITA do lead antes de reservar, remarcar ou cancelar.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta
from enum import StrEnum
from typing import Protocol
from uuid import UUID
from zoneinfo import ZoneInfo

from sdr.core.domain.eventos import EventoLead, TipoEvento
from sdr.core.domain.qualificacao import Ficha

DIAS_SEMANA = ("segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo")
SUGESTOES_PADRAO = 3


# ---------------------------------------------------------------------- entidades


@dataclass(frozen=True)
class Responsavel:
    """Quem atende o lead fora do chat (o nome do papel, em `titulo`, vem da vertical)."""

    id: UUID
    nome: str
    titulo: str
    especialidades: tuple[str, ...] = ()


@dataclass(frozen=True, eq=False)
class TipoAgendamento:
    """O que se agenda numa intenção (declarado pela vertical).

    `modalidades`: nome → rótulo para o lead. Com mais de uma, o lead escolhe.
    """

    nome: str
    rotulo: str
    modalidades: Mapping[str, str]
    duracao_min: int = 60

    def __post_init__(self) -> None:
        if not self.modalidades:
            raise ValueError(f"TipoAgendamento {self.nome!r} precisa de ao menos uma modalidade")

    @property
    def modalidade_unica(self) -> str | None:
        return next(iter(self.modalidades)) if len(self.modalidades) == 1 else None

    def modalidade_valida(self, modalidade: str | None) -> str | None:
        return modalidade if modalidade in self.modalidades else None


@dataclass(frozen=True)
class Slot:
    id: UUID
    responsavel_id: UUID
    inicio: datetime  # sempre com fuso
    fim: datetime


class StatusAgendamento(StrEnum):
    ATIVO = "ativo"
    CANCELADO = "cancelado"


@dataclass(frozen=True)
class Agendamento:
    id: UUID
    lead_id: UUID
    responsavel: Responsavel
    slot_id: UUID
    inicio: datetime
    fim: datetime
    tipo: str
    modalidade: str
    status: StatusAgendamento
    criado_em: datetime
    itens: tuple[str, ...] = ()  # itens do catálogo citados na conversa (contexto)


@dataclass(frozen=True)
class PedidoReserva:
    lead_id: UUID
    slot_id: UUID
    tipo: str
    modalidade: str
    chave_idempotencia: str  # mesma chave ⇒ mesmo agendamento (nunca reserva duas vezes)
    itens: tuple[str, ...] = ()


class SlotIndisponivelError(Exception):
    """O slot foi tomado (ou bloqueado) entre a oferta e a confirmação."""


class RegraAtribuicao(Protocol):
    """Quem pode atender um lead (implementada pela vertical)."""

    def ordenar(
        self, intencao: str, ficha: Ficha, responsaveis: Sequence[Responsavel]
    ) -> list[Responsavel]:
        """Responsáveis aptos, em ordem de preferência (vazio = ninguém atende)."""
        ...


# ---------------------------------------------------------------------- preferência


class Periodo(StrEnum):
    MANHA = "manha"
    TARDE = "tarde"
    NOITE = "noite"


FAIXAS_PERIODO: dict[Periodo, tuple[time, time]] = {
    Periodo.MANHA: (time(6), time(12)),
    Periodo.TARDE: (time(12), time(18)),
    Periodo.NOITE: (time(18), time(23, 59)),
}


def periodo_de(hora: time) -> Periodo:
    for periodo, (inicio, fim) in FAIXAS_PERIODO.items():
        if inicio <= hora < fim:
            return periodo
    return Periodo.NOITE


@dataclass(frozen=True)
class PreferenciaHorario:
    """Preferência do lead já estruturada (o LLM interpreta; a resolução é daqui).

    Os critérios se combinam (E). `dias_semana`: 0 = segunda … 6 = domingo; vale a
    próxima ocorrência e a seguinte (se esta quinta estiver lotada, serve a outra).
    """

    dias_semana: tuple[int, ...] = ()
    data: date | None = None
    daqui_a_dias: int | None = None  # 0 = hoje, 1 = amanhã
    periodo: Periodo | None = None
    apos_hora: int | None = None  # "depois das 18h" → começa às 18h ou depois
    antes_hora: int | None = None  # "antes das 10h" → começa antes das 10h
    hora: int | None = None  # "às 15h"

    @property
    def vazia(self) -> bool:
        return self == PreferenciaHorario()

    def datas(self, hoje: date) -> frozenset[date] | None:
        """Datas aceitas (None = qualquer data)."""
        if self.data is not None:
            return frozenset({self.data})
        if self.daqui_a_dias is not None:
            return frozenset({hoje + timedelta(days=self.daqui_a_dias)})
        if self.dias_semana:
            return frozenset(
                hoje + timedelta(days=(dia - hoje.weekday()) % 7 + 7 * semana)
                for dia in self.dias_semana
                for semana in (0, 1)
            )
        return None

    def atende(self, inicio_local: datetime, hoje: date) -> bool:
        datas = self.datas(hoje)
        if datas is not None and inicio_local.date() not in datas:
            return False
        hora = inicio_local.time()
        if self.periodo is not None:
            de, ate = FAIXAS_PERIODO[self.periodo]
            if not de <= hora < ate:
                return False
        if self.apos_hora is not None and hora < time(self.apos_hora):
            return False
        if self.antes_hora is not None and hora >= time(self.antes_hora):
            return False
        return self.hora is None or inicio_local.hour == self.hora

    def relaxadas(self) -> list["PreferenciaHorario"]:
        """Versões mais amplas, da mais próxima à mais distante (para propor alternativas):
        mantém o dia e solta o horário; depois mantém o horário e solta o dia."""
        so_dia = PreferenciaHorario(self.dias_semana, self.data, self.daqui_a_dias)
        so_horario = PreferenciaHorario(
            periodo=self.periodo, apos_hora=self.apos_hora, antes_hora=self.antes_hora
        )
        return [p for p in (so_dia, so_horario) if not p.vazia and p != self]


def sugerir(
    slots: Iterable[Slot],
    fuso: ZoneInfo,
    n: int = SUGESTOES_PADRAO,
    *,
    excluir: Iterable[UUID] = (),
) -> tuple[Slot, ...]:
    """Até `n` slots variados: o mais cedo primeiro; depois, dias e períodos diferentes."""
    fora = set(excluir)
    ordenados = sorted(
        (s for s in slots if s.id not in fora), key=lambda s: (s.inicio, str(s.responsavel_id))
    )
    escolhidos: list[Slot] = []

    def chave(slot: Slot) -> tuple[date, Periodo]:
        local = slot.inicio.astimezone(fuso)
        return local.date(), periodo_de(local.time())

    def dias() -> set[date]:
        return {chave(e)[0] for e in escolhidos}

    def dia_e_periodo_ineditos(slot: Slot) -> bool:
        dia, periodo = chave(slot)
        return dia not in dias() and periodo not in {chave(e)[1] for e in escolhidos}

    def dia_inedito(slot: Slot) -> bool:
        return chave(slot)[0] not in dias()

    def combinacao_inedita(slot: Slot) -> bool:
        return chave(slot) not in {chave(e) for e in escolhidos}

    def horario_inedito(slot: Slot) -> bool:
        return slot.inicio not in {e.inicio for e in escolhidos}

    for criterio in (dia_e_periodo_ineditos, dia_inedito, combinacao_inedita, horario_inedito):
        for slot in ordenados:
            if len(escolhidos) >= n:
                break
            if slot not in escolhidos and criterio(slot):
                escolhidos.append(slot)
    return tuple(sorted(escolhidos, key=lambda s: s.inicio))


def descrever_horario(inicio: datetime, fuso: ZoneInfo, hoje: date) -> str:
    """Ex.: "quinta (08/10) às 14h", "amanhã, terça (06/10) às 9h30".

    O dia da semana vai sempre: o lead fala em "quinta" e precisa reconhecer que "amanhã"
    é quinta (sem isso o LLM chegou a dizer "quinta não dá, mas amanhã sim").
    """
    local = inicio.astimezone(fuso)
    dia = local.date()
    semana = DIAS_SEMANA[dia.weekday()]
    if dia == hoje:
        nome = f"hoje, {semana}"
    elif dia == hoje + timedelta(days=1):
        nome = f"amanhã, {semana}"
    else:
        nome = semana
    hora = f"{local.hour}h" if local.minute == 0 else f"{local.hour}h{local.minute:02d}"
    return f"{nome} ({dia:%d/%m}) às {hora}"


# ---------------------------------------------------------------------- negociação


class Operacao(StrEnum):
    RESERVAR = "reservar"
    REMARCAR = "remarcar"
    CANCELAR = "cancelar"


@dataclass(frozen=True)
class Proposta:
    """O que será feito quando o lead confirmar (nada acontece sem a confirmação)."""

    operacao: Operacao
    slot: Slot | None = None  # None ao cancelar
    modalidade: str | None = None
    agendamento_id: UUID | None = None  # ao remarcar/cancelar


@dataclass(frozen=True)
class NegociacaoAgenda:
    """Estado da conversa sobre horários (persistido no lead entre turnos)."""

    ofertados: tuple[Slot, ...] = ()  # na ordem em que foram oferecidos ("a opção 2")
    proposta: Proposta | None = None  # aguardando confirmação explícita
    recusou: bool = False  # lead não quer agendar agora: não insistir


class AcaoAgenda(StrEnum):
    NENHUMA = "nenhuma"  # outro assunto
    PEDIR_HORARIOS = "pedir_horarios"
    PREFERENCIA = "preferencia"  # indicou dia/horário ou escolheu uma opção
    CONFIRMAR = "confirmar"
    RECUSAR = "recusar"
    REMARCAR = "remarcar"
    CANCELAR = "cancelar"


@dataclass(frozen=True)
class InterpretacaoAgenda:
    acao: AcaoAgenda
    preferencia: PreferenciaHorario = field(default_factory=PreferenciaHorario)
    opcao: int | None = None  # 1 = primeira opção oferecida
    modalidade: str | None = None


class TipoDecisao(StrEnum):
    OFERECER = "oferecer"
    PEDIR_CONFIRMACAO = "pedir_confirmacao"
    EXECUTAR = "executar"  # o lead confirmou: o caso de uso executa a proposta
    MANTER = "manter"  # já há agendamento e nada a mudar
    NAO_INSISTIR = "nao_insistir"
    SEM_DISPONIBILIDADE = "sem_disponibilidade"
    # resultados da execução
    AGENDADO = "agendado"
    REMARCADO = "remarcado"
    CANCELADO = "cancelado"


class Aviso(StrEnum):
    SEM_HORARIO_NA_PREFERENCIA = "sem_horario_na_preferencia"
    SLOT_TOMADO = "slot_tomado"
    FALTA_MODALIDADE = "falta_modalidade"
    PROPOSTA_RECUSADA = "proposta_recusada"
    SEM_AGENDAMENTO = "sem_agendamento"
    LEMBRETE = "lembrete"


@dataclass(frozen=True)
class Decisao:
    tipo: TipoDecisao
    negociacao: NegociacaoAgenda  # novo estado a persistir
    opcoes: tuple[Slot, ...] = ()
    proposta: Proposta | None = None
    aviso: Aviso | None = None


def oferecer(
    slots: Iterable[Slot],
    fuso: ZoneInfo,
    n: int = SUGESTOES_PADRAO,
    *,
    excluir: Iterable[UUID] = (),
    aviso: Aviso | None = None,
) -> Decisao:
    opcoes = sugerir(slots, fuso, n, excluir=excluir)
    if not opcoes:
        return Decisao(TipoDecisao.SEM_DISPONIBILIDADE, NegociacaoAgenda(), aviso=aviso)
    return Decisao(TipoDecisao.OFERECER, NegociacaoAgenda(ofertados=opcoes), opcoes, aviso=aviso)


def _escolher(
    interpretacao: InterpretacaoAgenda,
    ofertados: Sequence[Slot],
    livres: Sequence[Slot],
    fuso: ZoneInfo,
    hoje: date,
) -> Slot | None:
    livres_ids = {s.id for s in livres}
    pref = interpretacao.preferencia

    def atende(slot: Slot) -> bool:
        return pref.atende(slot.inicio.astimezone(fuso), hoje)

    # A opção numerada só vale se for compatível com o que o lead disse ("terça de manhã"
    # com opcao=1 apontando para quinta = o LLM chutou a opção; vale a preferência).
    opcao = interpretacao.opcao
    if opcao is not None and 1 <= opcao <= len(ofertados):
        escolhido = ofertados[opcao - 1]
        if escolhido.id in livres_ids and (pref.vazia or atende(escolhido)):
            return escolhido
    if pref.vazia:
        return None

    # Entre o que já foi oferecido e atende, vale o oferecido; senão, o mais cedo que atende.
    candidatos = [s for s in ofertados if s.id in livres_ids and atende(s)]
    candidatos = candidatos or sorted((s for s in livres if atende(s)), key=lambda s: s.inicio)
    return candidatos[0] if candidatos else None


def _alternativas(
    pref: PreferenciaHorario, livres: Sequence[Slot], fuso: ZoneInfo, hoje: date
) -> list[Slot]:
    for relaxada in pref.relaxadas():
        proximas = [s for s in livres if relaxada.atende(s.inicio.astimezone(fuso), hoje)]
        if proximas:
            return proximas
    return list(livres)


@dataclass(frozen=True)
class _Negociacao:
    """Um passo de `decidir`, com tudo que ele precisa à mão."""

    interpretacao: InterpretacaoAgenda
    negociacao: NegociacaoAgenda
    ativo: Agendamento | None
    livres: tuple[Slot, ...]
    tipo: TipoAgendamento
    hoje: date
    fuso: ZoneInfo
    n: int

    @property
    def acao(self) -> AcaoAgenda:
        return self.interpretacao.acao

    @property
    def modalidade_dita(self) -> str | None:
        return self.tipo.modalidade_valida(self.interpretacao.modalidade)

    def oferecer(
        self, slots: Iterable[Slot], *, excluir: Iterable[UUID] = (), aviso: Aviso | None = None
    ) -> Decisao:
        return oferecer(slots, self.fuso, self.n, excluir=excluir, aviso=aviso)

    def responder_proposta(self, proposta: Proposta) -> Decisao | None:
        """Lead respondendo a uma proposta pendente (None = falou de outra coisa)."""
        ativo = self.ativo
        if (
            proposta.operacao is not Operacao.CANCELAR
            and ativo is not None
            and proposta.slot is not None
            and ativo.slot_id == proposta.slot.id
        ):
            # Já executada (ex.: um turno anterior reservou e falhou antes de responder).
            return Decisao(TipoDecisao.MANTER, NegociacaoAgenda())
        if self.acao is AcaoAgenda.RECUSAR:
            if proposta.operacao is Operacao.CANCELAR or proposta.slot is None:
                return Decisao(TipoDecisao.MANTER, NegociacaoAgenda())
            return self.oferecer(
                self.livres, excluir={proposta.slot.id}, aviso=Aviso.PROPOSTA_RECUSADA
            )
        # Com cancelamento pendente, insistir em cancelar ("sim, pode cancelar") é confirmar.
        confirmou = self.acao is AcaoAgenda.CONFIRMAR or (
            proposta.operacao is Operacao.CANCELAR and self.acao is AcaoAgenda.CANCELAR
        )
        if not confirmou and self.acao is not AcaoAgenda.NENHUMA:
            return None
        modalidade = self.modalidade_dita or proposta.modalidade or self.tipo.modalidade_unica
        proposta = replace(proposta, modalidade=modalidade)
        falta_modalidade = proposta.operacao is not Operacao.CANCELAR and modalidade is None
        if confirmou and not falta_modalidade:
            return Decisao(TipoDecisao.EXECUTAR, NegociacaoAgenda(), proposta=proposta)
        # Assunto paralelo, ou só respondeu a modalidade: pede a confirmação de novo.
        return Decisao(
            TipoDecisao.PEDIR_CONFIRMACAO,
            replace(self.negociacao, proposta=proposta),
            proposta=proposta,
            aviso=Aviso.FALTA_MODALIDADE if falta_modalidade else None,
        )

    def pedir_cancelamento(self) -> Decisao:
        if self.ativo is None:
            return Decisao(TipoDecisao.MANTER, NegociacaoAgenda(), aviso=Aviso.SEM_AGENDAMENTO)
        cancelar = Proposta(Operacao.CANCELAR, agendamento_id=self.ativo.id)
        return Decisao(
            TipoDecisao.PEDIR_CONFIRMACAO, NegociacaoAgenda(proposta=cancelar), proposta=cancelar
        )

    @property
    def indicou_horario(self) -> bool:
        i = self.interpretacao
        return (not i.preferencia.vazia or i.opcao is not None) and self.acao in (
            AcaoAgenda.PREFERENCIA,
            AcaoAgenda.REMARCAR,
            AcaoAgenda.CONFIRMAR,
            AcaoAgenda.PEDIR_HORARIOS,
        )

    def propor_horario(self) -> Decisao:
        """O lead indicou um horário: propõe o melhor slot real (ou alternativas)."""
        ativo = self.ativo
        escolhido = _escolher(
            self.interpretacao, self.negociacao.ofertados, self.livres, self.fuso, self.hoje
        )
        if escolhido is None:
            alternativas = _alternativas(
                self.interpretacao.preferencia, self.livres, self.fuso, self.hoje
            )
            return self.oferecer(alternativas, aviso=Aviso.SEM_HORARIO_NA_PREFERENCIA)
        modalidade = (
            self.modalidade_dita
            or (ativo.modalidade if ativo else None)
            or self.tipo.modalidade_unica
        )
        nova = Proposta(
            Operacao.REMARCAR if ativo else Operacao.RESERVAR,
            escolhido,
            modalidade,
            ativo.id if ativo else None,
        )
        return Decisao(
            TipoDecisao.PEDIR_CONFIRMACAO,
            NegociacaoAgenda(ofertados=self.negociacao.ofertados, proposta=nova),
            proposta=nova,
            aviso=Aviso.FALTA_MODALIDADE if modalidade is None else None,
        )

    def seguir_sem_horario(self) -> Decisao:
        if self.acao in (AcaoAgenda.PEDIR_HORARIOS, AcaoAgenda.REMARCAR):
            return self.oferecer(self.livres)
        if self.ativo is not None:
            return Decisao(TipoDecisao.MANTER, NegociacaoAgenda())
        if self.acao is AcaoAgenda.RECUSAR or (
            self.negociacao.recusou and self.acao is AcaoAgenda.NENHUMA
        ):
            return Decisao(TipoDecisao.NAO_INSISTIR, NegociacaoAgenda(recusou=True))
        # Sem proposta e sem agendamento: oferece horários (ou relembra os já oferecidos).
        livres_ids = {s.id for s in self.livres}
        ainda_livres = [s for s in self.negociacao.ofertados if s.id in livres_ids]
        if not ainda_livres or self.acao is not AcaoAgenda.NENHUMA:
            return self.oferecer(self.livres)
        extras = sugerir(self.livres, self.fuso, self.n, excluir={s.id for s in ainda_livres})
        opcoes = tuple([*ainda_livres, *extras][: self.n])
        return Decisao(
            TipoDecisao.OFERECER, NegociacaoAgenda(ofertados=opcoes), opcoes, aviso=Aviso.LEMBRETE
        )


def decidir(
    interpretacao: InterpretacaoAgenda,
    negociacao: NegociacaoAgenda,
    ativo: Agendamento | None,
    livres: Sequence[Slot],
    tipo: TipoAgendamento,
    *,
    hoje: date,
    fuso: ZoneInfo,
    n: int = SUGESTOES_PADRAO,
) -> Decisao:
    """Próximo passo da negociação. Nada é reservado, remarcado ou cancelado sem uma
    proposta pendente E a confirmação explícita do lead (`AcaoAgenda.CONFIRMAR`)."""
    passo = _Negociacao(interpretacao, negociacao, ativo, tuple(livres), tipo, hoje, fuso, n)
    if negociacao.proposta is not None:
        decisao = passo.responder_proposta(negociacao.proposta)
        if decisao is not None:
            return decisao
    if interpretacao.acao is AcaoAgenda.CANCELAR:
        return passo.pedir_cancelamento()
    if passo.indicou_horario:
        return passo.propor_horario()
    return passo.seguir_sem_horario()


# ---------------------------------------------------------------------- eventos


def evento_agendamento(
    tipo: TipoEvento, agendamento: Agendamento, momento: datetime, **extra: object
) -> EventoLead:
    r = agendamento.responsavel
    payload: dict[str, object] = {
        "agendamento_id": str(agendamento.id),
        "tipo": agendamento.tipo,
        "modalidade": agendamento.modalidade,
        "inicio": agendamento.inicio.isoformat(),
        "fim": agendamento.fim.isoformat(),
        "responsavel_id": str(r.id),
        "responsavel_nome": r.nome,
        "responsavel_titulo": r.titulo,
        "itens": list(agendamento.itens),
        **extra,
    }
    return EventoLead(agendamento.lead_id, tipo, momento, payload)
