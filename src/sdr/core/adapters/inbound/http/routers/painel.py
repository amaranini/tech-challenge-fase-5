"""Consultas do dashboard (read models): visão geral, leads filtráveis e follow-ups.

Só leitura. Período em `inicio`/`fim` (ISO 8601; padrão: últimos 14 dias). Fila, agenda e
detalhe do lead usam as rotas que já existem (/atendimentos, /agendamentos, /leads/{id},
/conversas/{id}/mensagens, /leads/{id}/resumo).
"""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from sdr.core.adapters.inbound.http.dependencias import obter_consultar_painel
from sdr.core.application.consultas.painel import (
    ConsultarPainel,
    FiltroLeads,
    LeadPainel,
    Periodo,
    PeriodoInvalidoError,
)
from sdr.core.domain.conversa import agora

router = APIRouter(prefix="/painel", tags=["painel"])

Inicio = Annotated[datetime | None, Query(description="Início do período (padrão: fim − 14 dias)")]
Fim = Annotated[datetime | None, Query(description="Fim do período (padrão: agora)")]


class PeriodoResposta(BaseModel):
    inicio: datetime
    fim: datetime


class ContagemResposta(BaseModel):
    chave: str
    total: int


class EtapaFunilResposta(BaseModel):
    etapa: str
    total: int
    conversao_da_anterior: float | None


class VisaoGeralResposta(BaseModel):
    periodo: PeriodoResposta
    novos: int
    qualificados: int
    agendados: int
    handoffs: int
    sla_excedidos: int
    mediana_primeira_resposta_s: float | None
    mediana_ate_qualificar_s: float | None
    funil: list[EtapaFunilResposta]
    por_intencao: list[ContagemResposta]
    por_classificacao: list[ContagemResposta]
    por_canal: list[ContagemResposta]
    por_estado_atendimento: list[ContagemResposta]


class LeadPainelResposta(BaseModel):
    lead_id: str  # público: "lead-ana" (web) ou "whatsapp:+55..."
    canal: str
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

    @classmethod
    def de(cls, lead: LeadPainel) -> "LeadPainelResposta":
        publico = lead.remetente_id if lead.canal == "web" else f"{lead.canal}:{lead.remetente_id}"
        return cls(
            lead_id=publico,
            canal=lead.canal,
            nome=lead.nome,
            intencao=lead.intencao,
            score=lead.score,
            classificacao=lead.classificacao,
            estado_atendimento=lead.estado_atendimento,
            proxima_acao=lead.proxima_acao,
            criado_em=lead.criado_em,
            ultima_interacao_em=lead.ultima_interacao_em,
            total_mensagens=lead.total_mensagens,
            agendado=lead.agendado,
            opt_out=lead.opt_out,
            demo=lead.demo,
        )


class FollowUpPendenteResposta(BaseModel):
    lead_id: str
    tipo: str
    etapa: int
    executar_em: datetime
    status: str


class EventoFollowUpResposta(BaseModel):
    lead_id: str
    tipo: str
    ocorrido_em: datetime
    detalhe: str | None


class FollowUpsResposta(BaseModel):
    periodo: PeriodoResposta
    pendentes: list[FollowUpPendenteResposta]
    enviados: int
    enviados_por_template: int
    leads_com_followup: int
    reengajados: int
    taxa_reengajamento: float | None
    opt_outs: int
    encerrados_por_inatividade: int
    recentes: list[EventoFollowUpResposta]


def _publico(canal: str, remetente: str) -> str:
    return remetente if canal == "web" else f"{canal}:{remetente}"


def _periodo(inicio: datetime | None, fim: datetime | None) -> Periodo:
    try:
        return Periodo.de(inicio, fim, agora())
    except PeriodoInvalidoError as erro:
        raise HTTPException(422, str(erro)) from None


def _contagens(itens: tuple) -> list[ContagemResposta]:  # type: ignore[type-arg]
    return [ContagemResposta(chave=c.chave, total=c.total) for c in itens]


@router.get("/visao-geral", response_model=VisaoGeralResposta)
async def visao_geral(
    painel: Annotated[ConsultarPainel, Depends(obter_consultar_painel)],
    inicio: Inicio = None,
    fim: Fim = None,
) -> VisaoGeralResposta:
    """KPIs, funil (coorte dos leads criados no período) e distribuições."""
    v = await painel.visao_geral(_periodo(inicio, fim))
    return VisaoGeralResposta(
        periodo=PeriodoResposta(inicio=v.periodo.inicio, fim=v.periodo.fim),
        novos=v.novos,
        qualificados=v.qualificados,
        agendados=v.agendados,
        handoffs=v.handoffs,
        sla_excedidos=v.sla_excedidos,
        mediana_primeira_resposta_s=v.mediana_primeira_resposta_s,
        mediana_ate_qualificar_s=v.mediana_ate_qualificar_s,
        funil=[
            EtapaFunilResposta(
                etapa=e.etapa, total=e.total, conversao_da_anterior=e.conversao_da_anterior
            )
            for e in v.funil
        ],
        por_intencao=_contagens(v.por_intencao),
        por_classificacao=_contagens(v.por_classificacao),
        por_canal=_contagens(v.por_canal),
        por_estado_atendimento=_contagens(v.por_estado_atendimento),
    )


@router.get("/leads", response_model=list[LeadPainelResposta])
async def leads(
    painel: Annotated[ConsultarPainel, Depends(obter_consultar_painel)],
    *,
    inicio: Inicio = None,
    fim: Fim = None,
    canal: str | None = None,
    intencao: Annotated[str | None, Query(description='"indefinida" = sem intenção')] = None,
    estado_atendimento: str | None = None,
    classificacao: str | None = None,
    score_min: Annotated[int | None, Query(ge=0, le=100)] = None,
    score_max: Annotated[int | None, Query(ge=0, le=100)] = None,
    demo: Annotated[bool | None, Query(description="true/false; vazio = todos")] = None,
    limite: Annotated[int, Query(ge=1, le=500)] = 200,
) -> list[LeadPainelResposta]:
    """Leads criados no período (mais recentes por última interação), com filtros."""
    filtro = FiltroLeads(
        periodo=_periodo(inicio, fim),
        canal=canal,
        intencao=intencao,
        estado_atendimento=estado_atendimento,
        classificacao=classificacao,
        score_min=score_min,
        score_max=score_max,
        apenas_demo=demo,
        limite=limite,
    )
    return [LeadPainelResposta.de(lead) for lead in await painel.leads(filtro)]


@router.get("/followups", response_model=FollowUpsResposta)
async def followups(
    painel: Annotated[ConsultarPainel, Depends(obter_consultar_painel)],
    inicio: Inicio = None,
    fim: Fim = None,
) -> FollowUpsResposta:
    """Pendentes (agora) e, no período: enviados, reengajamentos, opt-outs, encerramentos."""
    f = await painel.followups(_periodo(inicio, fim))
    return FollowUpsResposta(
        periodo=PeriodoResposta(inicio=f.periodo.inicio, fim=f.periodo.fim),
        pendentes=[
            FollowUpPendenteResposta(
                lead_id=_publico(p.canal, p.remetente_id),
                tipo=p.tipo,
                etapa=p.etapa,
                executar_em=p.executar_em,
                status=p.status,
            )
            for p in f.pendentes
        ],
        enviados=f.enviados,
        enviados_por_template=f.enviados_por_template,
        leads_com_followup=f.leads_com_followup,
        reengajados=f.reengajados,
        taxa_reengajamento=f.taxa_reengajamento,
        opt_outs=f.opt_outs,
        encerrados_por_inatividade=f.encerrados_por_inatividade,
        recentes=[
            EventoFollowUpResposta(
                lead_id=_publico(e.canal, e.remetente_id),
                tipo=e.tipo,
                ocorrido_em=e.ocorrido_em,
                detalhe=e.detalhe,
            )
            for e in f.recentes
        ],
    )
