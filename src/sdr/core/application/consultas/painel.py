"""Read models do dashboard: calculados a partir de lead_eventos e das tabelas existentes.

Só leitura, separados dos comandos: o port `ConsultasPainelPort` é implementado em SQL
(agregações no banco) e os casos de uso aqui só validam o período e montam o funil.

Funil por COORTE: os leads criados no período e até onde cada um chegou
(novo → com intenção → qualificado → agendado). Quem chegou a uma etapa conta também
nas anteriores (ex.: agendou antes de completar a ficha), então o funil nunca "sobe".
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import IntEnum
from typing import Protocol
from uuid import UUID

PERIODO_PADRAO = timedelta(days=14)
PERIODO_MAXIMO = timedelta(days=366)


class EtapaFunil(IntEnum):
    NOVO = 0
    COM_INTENCAO = 1
    QUALIFICADO = 2
    AGENDADO = 3

    @property
    def rotulo(self) -> str:
        return {0: "novos", 1: "com intenção", 2: "qualificados", 3: "agendados"}[self.value]


class PeriodoInvalidoError(ValueError):
    pass


@dataclass(frozen=True)
class Periodo:
    inicio: datetime
    fim: datetime

    @classmethod
    def de(cls, inicio: datetime | None, fim: datetime | None, agora: datetime) -> "Periodo":
        fim = fim or agora
        inicio = inicio or fim - PERIODO_PADRAO
        if inicio >= fim:
            raise PeriodoInvalidoError("início precisa ser antes do fim")
        if fim - inicio > PERIODO_MAXIMO:
            raise PeriodoInvalidoError("período máximo: 366 dias")
        return cls(inicio, fim)


@dataclass(frozen=True)
class Contagem:
    chave: str
    total: int


@dataclass(frozen=True)
class LeadNoFunil:
    """Uma linha da coorte: o que o SQL devolve para o funil e os tempos."""

    lead_id: UUID
    etapa: EtapaFunil
    segundos_primeira_resposta: float | None
    segundos_ate_qualificar: float | None


@dataclass(frozen=True)
class EtapaDoFunil:
    etapa: str
    total: int
    conversao_da_anterior: float | None  # 0..1; None na primeira


@dataclass(frozen=True)
class VisaoGeral:
    periodo: Periodo
    novos: int
    qualificados: int
    agendados: int
    handoffs: int
    sla_excedidos: int
    mediana_primeira_resposta_s: float | None
    mediana_ate_qualificar_s: float | None
    funil: tuple[EtapaDoFunil, ...]
    por_intencao: tuple[Contagem, ...]
    por_classificacao: tuple[Contagem, ...]
    por_canal: tuple[Contagem, ...]
    por_estado_atendimento: tuple[Contagem, ...]


@dataclass(frozen=True)
class DistribuicoesCoorte:
    por_intencao: tuple[Contagem, ...]
    por_classificacao: tuple[Contagem, ...]
    por_canal: tuple[Contagem, ...]
    por_estado_atendimento: tuple[Contagem, ...]
    handoffs: int
    sla_excedidos: int


@dataclass(frozen=True)
class FiltroLeads:
    periodo: Periodo | None = None
    canal: str | None = None
    intencao: str | None = None  # "indefinida" = sem intenção
    estado_atendimento: str | None = None
    classificacao: str | None = None
    score_min: int | None = None
    score_max: int | None = None
    apenas_demo: bool | None = None  # None = todos
    limite: int = 200


@dataclass(frozen=True)
class LeadPainel:
    lead_id: UUID
    canal: str
    remetente_id: str
    nome: str | None
    intencao: str | None
    score: int | None
    classificacao: str | None
    estado_atendimento: str
    proxima_acao: str | None
    criado_em: datetime
    ultima_interacao_em: datetime | None
    total_mensagens: int
    agendado: bool
    opt_out: bool
    demo: bool


@dataclass(frozen=True)
class FollowUpPendente:
    lead_id: UUID
    canal: str
    remetente_id: str
    tipo: str
    etapa: int
    executar_em: datetime
    status: str


@dataclass(frozen=True)
class EventoFollowUp:
    lead_id: UUID
    canal: str
    remetente_id: str
    tipo: str
    ocorrido_em: datetime
    detalhe: str | None = None


@dataclass(frozen=True)
class PainelFollowUps:
    periodo: Periodo
    pendentes: tuple[FollowUpPendente, ...]
    enviados: int
    enviados_por_template: int
    leads_com_followup: int
    reengajados: int
    opt_outs: int
    encerrados_por_inatividade: int
    recentes: tuple[EventoFollowUp, ...] = field(default_factory=tuple)

    @property
    def taxa_reengajamento(self) -> float | None:
        return self.reengajados / self.leads_com_followup if self.leads_com_followup else None


class ConsultasPainelPort(Protocol):
    async def coorte(self, periodo: Periodo) -> list[LeadNoFunil]: ...

    async def distribuicoes(self, periodo: Periodo) -> DistribuicoesCoorte: ...

    async def leads(self, filtro: FiltroLeads) -> list[LeadPainel]: ...

    async def followups(self, periodo: Periodo, limite_recentes: int = 50) -> PainelFollowUps: ...


def mediana(valores: Sequence[float]) -> float | None:
    ordenados = sorted(valores)
    if not ordenados:
        return None
    meio = len(ordenados) // 2
    if len(ordenados) % 2:
        return ordenados[meio]
    return (ordenados[meio - 1] + ordenados[meio]) / 2


def montar_funil(coorte: Sequence[LeadNoFunil]) -> tuple[EtapaDoFunil, ...]:
    etapas = []
    anterior: int | None = None
    for etapa in EtapaFunil:
        total = sum(1 for lead in coorte if lead.etapa >= etapa)
        conversao = (total / anterior) if anterior else None
        etapas.append(EtapaDoFunil(etapa.rotulo, total, conversao))
        anterior = total
    return tuple(etapas)


class ConsultarPainel:
    def __init__(self, consultas: ConsultasPainelPort) -> None:
        self._consultas = consultas

    async def visao_geral(self, periodo: Periodo) -> VisaoGeral:
        coorte = await self._consultas.coorte(periodo)
        dist = await self._consultas.distribuicoes(periodo)
        funil = montar_funil(coorte)
        return VisaoGeral(
            periodo=periodo,
            novos=funil[EtapaFunil.NOVO].total,
            qualificados=funil[EtapaFunil.QUALIFICADO].total,
            agendados=funil[EtapaFunil.AGENDADO].total,
            handoffs=dist.handoffs,
            sla_excedidos=dist.sla_excedidos,
            mediana_primeira_resposta_s=mediana(
                [
                    x.segundos_primeira_resposta
                    for x in coorte
                    if x.segundos_primeira_resposta is not None
                ]
            ),
            mediana_ate_qualificar_s=mediana(
                [x.segundos_ate_qualificar for x in coorte if x.segundos_ate_qualificar is not None]
            ),
            funil=funil,
            por_intencao=dist.por_intencao,
            por_classificacao=dist.por_classificacao,
            por_canal=dist.por_canal,
            por_estado_atendimento=dist.por_estado_atendimento,
        )

    async def leads(self, filtro: FiltroLeads) -> list[LeadPainel]:
        return await self._consultas.leads(filtro)

    async def followups(self, periodo: Periodo) -> PainelFollowUps:
        return await self._consultas.followups(periodo)
