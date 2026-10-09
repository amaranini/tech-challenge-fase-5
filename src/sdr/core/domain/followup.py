"""Follow-up: quando e se o assistente volta a falar com um lead que parou de responder.

Regras puras (testáveis com relógio fake):
- a CADÊNCIA é da vertical, por situação do lead (ex.: D+1, D+3 e encerramento em D+7);
- a ELEGIBILIDADE é do core: não manda se o lead respondeu, se não está com a IA, se tem
  agendamento futuro, se pediu para parar (opt-out) ou se a conversa foi encerrada; fora do
  horário de atendimento, adia para a próxima abertura (desligável);
- na fila do humano, em vez de follow-up comercial, vale o SLA do handoff (só conta o tempo
  dentro do horário de atendimento);
- lembrete de agendamento sai X horas antes, pela mesma fila.

O conteúdo de cada mensagem (objetivo e template do canal) é da vertical; o core só garante
as regras de toda mensagem ativa (retomar do ponto, sem pressão, nada inventado).
"""

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum
from uuid import UUID, uuid4

from sdr.core.domain.atendimento import Atendimento, EstadoAtendimento, HorarioAtendimento
from sdr.core.domain.qualificacao import Qualificacao
from sdr.core.domain.template import TemplateLogico


class TipoFollowUp(StrEnum):
    RETOMADA = "retomada"  # follow-up comercial da cadência
    LEMBRETE_AGENDAMENTO = "lembrete_agendamento"
    SLA_HANDOFF = "sla_handoff"  # checagem da espera na fila do humano


class StatusFollowUp(StrEnum):
    PENDENTE = "pendente"
    PROCESSANDO = "processando"  # reservado por um worker (SKIP LOCKED)
    ENVIADO = "enviado"  # mensagem enviada (ou alerta emitido, no SLA)
    CANCELADO = "cancelado"  # não vale mais (lead respondeu, opt-out, agendou...)


class UnidadeTempo(StrEnum):
    DIAS = "dias"
    MINUTOS = "minutos"  # demo: a cadência inteira cabe em minutos

    def duracao(self, quantidade: int) -> timedelta:
        return (
            timedelta(days=quantidade)
            if self is UnidadeTempo.DIAS
            else timedelta(minutes=quantidade)
        )


@dataclass(frozen=True)
class EtapaCadencia:
    """Uma mensagem da cadência — o QUE dizer e por qual template é da vertical."""

    apos: int  # unidades desde a etapa anterior (a 1ª: desde a última resposta)
    objetivo: str  # orientação para a mensagem
    template: TemplateLogico  # fora da janela de conversa do canal (ex.: 24h no WhatsApp)
    encerramento: bool = False  # a última: despedida educada, porta aberta


@dataclass(frozen=True)
class ModeloLembrete:
    """Lembrete de agendamento da vertical (sem ele, o core não programa lembretes)."""

    objetivo: str
    template: TemplateLogico  # fora da janela de conversa do canal


@dataclass(frozen=True)
class Cadencia:
    etapas: tuple[EtapaCadencia, ...]

    def etapa(self, numero: int) -> EtapaCadencia | None:
        return self.etapas[numero - 1] if 1 <= numero <= len(self.etapas) else None


class SituacaoLead(StrEnum):
    """Situação comercial do lead, que escolhe a cadência da vertical."""

    DESCOBERTA = "descoberta"  # ainda sem intenção
    QUALIFICACAO = "qualificacao"  # intenção, mas não qualificado
    QUALIFICADO = "qualificado"  # qualificado, sem agendamento


def situacao_do_lead(qualificacao: Qualificacao) -> SituacaoLead:
    if qualificacao.intencao_atual is None:
        return SituacaoLead.DESCOBERTA
    if qualificacao.proxima_acao is None:
        return SituacaoLead.QUALIFICACAO
    return SituacaoLead.QUALIFICADO


@dataclass(frozen=True)
class FollowUp:
    id: UUID
    lead_id: UUID
    tipo: TipoFollowUp
    executar_em: datetime
    criado_em: datetime
    etapa: int = 1
    situacao: SituacaoLead | None = None  # retomada: qual cadência
    status: StatusFollowUp = StatusFollowUp.PENDENTE
    motivo: str | None = None  # por que foi cancelado/adiado
    referencia: str | None = None  # agendamento (lembrete) ou entrada na fila (SLA)
    ignorar_horario: bool = False  # demo: "simular inatividade" não espera o expediente

    @classmethod
    def novo(
        cls,
        lead_id: UUID,
        tipo: TipoFollowUp,
        executar_em: datetime,
        agora: datetime,
        **extra: object,
    ) -> "FollowUp":
        return cls(uuid4(), lead_id, tipo, executar_em, agora, **extra)  # type: ignore[arg-type]

    def adiar(self, para: datetime, motivo: str) -> "FollowUp":
        return replace(self, executar_em=para, motivo=motivo, status=StatusFollowUp.PENDENTE)


class MotivoInelegivel(StrEnum):
    LEAD_RESPONDEU = "lead_respondeu"
    FORA_DA_IA = "atendimento_nao_e_da_ia"
    AGENDAMENTO_FUTURO = "tem_agendamento_futuro"
    OPT_OUT = "opt_out"
    CONVERSA_ENCERRADA = "conversa_encerrada"
    FORA_DO_HORARIO = "fora_do_horario"  # adia (não cancela)


@dataclass(frozen=True)
class ContextoElegibilidade:
    atendimento: Atendimento
    opt_out: bool
    conversa_aberta: bool
    tem_agendamento_futuro: bool
    lead_falou_por_ultimo_em: datetime | None  # última mensagem do lead


def motivo_inelegivel(
    followup: FollowUp,
    ctx: ContextoElegibilidade,
    agora: datetime,
    horario: HorarioAtendimento,
    *,
    respeitar_horario: bool = True,
) -> MotivoInelegivel | None:
    """None = pode enviar agora. FORA_DO_HORARIO = adiar; os demais = cancelar."""
    retomada = followup.tipo is TipoFollowUp.RETOMADA
    respondeu = bool(
        ctx.lead_falou_por_ultimo_em and ctx.lead_falou_por_ultimo_em > followup.criado_em
    )
    regras = (
        (ctx.opt_out, MotivoInelegivel.OPT_OUT),
        (not ctx.conversa_aberta, MotivoInelegivel.CONVERSA_ENCERRADA),
        (
            ctx.atendimento.estado is not EstadoAtendimento.ATENDIMENTO_IA,
            MotivoInelegivel.FORA_DA_IA,
        ),
        (retomada and respondeu, MotivoInelegivel.LEAD_RESPONDEU),
        (retomada and ctx.tem_agendamento_futuro, MotivoInelegivel.AGENDAMENTO_FUTURO),
        (
            respeitar_horario and not followup.ignorar_horario and not horario.aberto(agora),
            MotivoInelegivel.FORA_DO_HORARIO,
        ),
    )
    return next((motivo for vale, motivo in regras if vale), None)


def requer_template(
    lead_falou_por_ultimo_em: datetime | None, agora: datetime, janela: timedelta
) -> bool:
    """Fora da janela de conversa do canal (ex.: 24h no WhatsApp), só com template aprovado."""
    return lead_falou_por_ultimo_em is None or agora - lead_falou_por_ultimo_em > janela


# ---------------------------------------------------------------------- SLA do handoff


def tempo_util(inicio: datetime, fim: datetime, horario: HorarioAtendimento) -> timedelta:
    """Quanto de [inicio, fim) caiu dentro do horário de atendimento."""
    total = timedelta()
    cursor = inicio
    while cursor < fim:
        if horario.aberto(cursor):
            total += min(timedelta(minutes=1), fim - cursor)
            cursor += timedelta(minutes=1)
        else:
            cursor = min(horario.proxima_abertura(cursor), fim)
    return total


def proxima_checagem_sla(
    na_fila_desde: datetime, sla: timedelta, agora: datetime, horario: HorarioAtendimento
) -> datetime | None:
    """None = SLA estourado (alerta agora); senão, quando checar de novo."""
    falta = sla - tempo_util(na_fila_desde, agora, horario)
    if falta <= timedelta():
        return None
    base = horario.proxima_abertura(agora)
    return base + falta


def cadencias_validas(cadencias: Mapping[SituacaoLead, Cadencia]) -> None:
    for situacao, cadencia in cadencias.items():
        if not cadencia.etapas or not cadencia.etapas[-1].encerramento:
            raise ValueError(f"cadência {situacao.value!r} precisa terminar em encerramento")
