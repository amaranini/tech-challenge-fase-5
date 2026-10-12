"""Qualidade da IA: métricas online por versão do agente, eval offline e feedback do
resumo. Só para a equipe (X-API-Key). Definições em core/application/consultas/qualidade.py.
"""

from collections.abc import Mapping
from datetime import datetime
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from pydantic import BaseModel, Field

from sdr.core.adapters.inbound.http.dependencias import obter_dependencias
from sdr.core.adapters.inbound.http.lead_publico import identificar
from sdr.core.application.consultas.painel import Periodo, PeriodoInvalidoError
from sdr.core.application.consultas.qualidade import (
    ABANDONO_HORAS_PADRAO,
    AvaliacaoOffline,
    ConsultarQualidade,
    MetricasVersao,
    RegistrarAvaliacaoOffline,
)
from sdr.core.application.use_cases.avaliar_resumo import (
    COMENTARIO_MAX,
    AvaliacaoResumo,
    AvaliarResumo,
    ResumoInexistenteError,
)
from sdr.core.domain.conversa import agora

router = APIRouter(tags=["qualidade"])


def _dependencia(nome: str) -> Any:
    def obter(request: Request) -> Any:
        caso = getattr(obter_dependencias(request), nome)()
        if caso is None:
            raise HTTPException(404, "qualidade da IA não configurada")
        return caso

    return obter


obter_consultar = _dependencia("consultar_qualidade")
obter_registrar = _dependencia("registrar_avaliacao_offline")
obter_avaliar_resumo = _dependencia("avaliar_resumo")


class MetricasVersaoResposta(BaseModel):
    versao: str
    persona: str
    modelo: str
    prompts_core: str
    conversas: int
    taxa_pedido_humano: float | None
    taxa_opt_out: float | None
    taxa_abandono: float | None
    taxa_qualificacao: float | None
    turnos_ate_qualificar_medio: float | None
    segundos_ate_qualificar_medio: float | None
    respostas: int
    respostas_reprovadas: int
    respostas_bloqueadas: int
    tentativas_injecao: int
    latencia_p50_s: float | None
    latencia_p95_s: float | None
    custo_total_usd: float | None
    custo_por_conversa_usd: float | None
    custo_por_agendamento_usd: float | None
    agendamentos: int

    @classmethod
    def de(cls, m: MetricasVersao) -> "MetricasVersaoResposta":
        return cls(**m.__dict__)


class PontoAbandonoResposta(BaseModel):
    versao: str
    ponto: str
    total: int


class IncidenteResposta(BaseModel):
    tipo: str
    ocorrido_em: datetime
    lead_id: str
    detalhe: str
    trace_url: str | None
    sessao_url: str | None = Field(description="Sessão do lead no Langfuse")


class QualidadeOnlineResposta(BaseModel):
    inicio: datetime
    fim: datetime
    abandono_horas: float
    versoes: list[MetricasVersaoResposta]
    abandonos: list[PontoAbandonoResposta]
    incidentes: list[IncidenteResposta]
    langfuse_projeto_url: str | None
    resumos_avaliados: int
    taxa_resumo_util: float | None


class AvaliacaoOfflineEntrada(BaseModel):
    executada_em: datetime
    versao: dict[str, Any] = Field(description="Versão do agente avaliada (GET /qualidade/versao)")
    metricas: dict[str, Any]
    contexto: dict[str, Any] = Field(default_factory=dict)


class AvaliacaoOfflineResposta(AvaliacaoOfflineEntrada):
    id: UUID

    @classmethod
    def de(cls, a: AvaliacaoOffline) -> "AvaliacaoOfflineResposta":
        return cls(
            id=a.id,
            executada_em=a.executada_em,
            versao=dict(a.versao),
            metricas=dict(a.metricas),
            contexto=dict(a.contexto),
        )


class AvaliacaoResumoEntrada(BaseModel):
    util: bool
    responsavel: str | None = Field(default=None, max_length=100)
    comentario: str | None = Field(default=None, max_length=COMENTARIO_MAX)
    versao: int | None = Field(default=None, ge=1, description="Vazio = a versão atual")


def _sessao(projeto: str | None, lead_id: UUID) -> str | None:
    return f"{projeto}/sessions/{lead_id}" if projeto else None


@router.get("/qualidade/online", response_model=QualidadeOnlineResposta)
async def online(
    consultar: Annotated[ConsultarQualidade, Depends(obter_consultar)],
    inicio: Annotated[datetime | None, Query()] = None,
    fim: Annotated[datetime | None, Query()] = None,
    abandono_horas: Annotated[float, Query(gt=0, le=24 * 30)] = ABANDONO_HORAS_PADRAO,
    apenas_reais: Annotated[bool, Query(description="Sem os leads sintéticos (demo)")] = False,
) -> QualidadeOnlineResposta:
    """Métricas das conversas do período (leads criados nele), por versão do agente."""
    try:
        periodo = Periodo.de(inicio, fim, agora())
    except PeriodoInvalidoError as erro:
        raise HTTPException(422, str(erro)) from erro
    q = await consultar.online(
        periodo, agora(), abandono_horas=abandono_horas, apenas_reais=apenas_reais
    )
    return QualidadeOnlineResposta(
        inicio=q.periodo.inicio,
        fim=q.periodo.fim,
        abandono_horas=q.abandono_horas,
        versoes=[MetricasVersaoResposta.de(v) for v in q.versoes],
        abandonos=[PontoAbandonoResposta(**a.__dict__) for a in q.abandonos],
        incidentes=[
            IncidenteResposta(
                tipo=i.tipo,
                ocorrido_em=i.ocorrido_em,
                lead_id=i.lead_publico,
                detalhe=i.detalhe,
                trace_url=i.trace_url,
                sessao_url=_sessao(q.langfuse_projeto_url, i.lead_id),
            )
            for i in q.incidentes
        ],
        langfuse_projeto_url=q.langfuse_projeto_url,
        resumos_avaliados=q.resumos_avaliados,
        taxa_resumo_util=q.taxa_resumo_util,
    )


@router.get("/qualidade/versao")
async def versao(request: Request) -> Mapping[str, Any]:
    """Versão do agente em produção agora: persona, prompts do core e modelo de cada nó."""
    return obter_dependencias(request).versao_agente


@router.get("/qualidade/offline", response_model=list[AvaliacaoOfflineResposta])
async def avaliacoes_offline(
    consultar: Annotated[ConsultarQualidade, Depends(obter_consultar)],
    limite: Annotated[int, Query(ge=1, le=50)] = 10,
) -> list[AvaliacaoOfflineResposta]:
    """Rodadas do eval offline (evals/runner.py), da mais recente para a mais antiga."""
    return [AvaliacaoOfflineResposta.de(a) for a in await consultar.avaliacoes_offline(limite)]


@router.post("/qualidade/offline", response_model=AvaliacaoOfflineResposta, status_code=201)
async def registrar_offline(
    entrada: AvaliacaoOfflineEntrada,
    registrar: Annotated[RegistrarAvaliacaoOffline, Depends(obter_registrar)],
) -> AvaliacaoOfflineResposta:
    """O runner da avaliação grava aqui o resumo de cada rodada."""
    avaliacao = await registrar.executar(
        AvaliacaoOffline(
            uuid4(), entrada.executada_em, entrada.versao, entrada.metricas, entrada.contexto
        )
    )
    return AvaliacaoOfflineResposta.de(avaliacao)


@router.post("/leads/{lead_id}/resumo/avaliacao", status_code=201)
async def avaliar_resumo(
    lead_id: Annotated[str, Path(min_length=1, max_length=100)],
    entrada: AvaliacaoResumoEntrada,
    avaliar: Annotated[AvaliarResumo, Depends(obter_avaliar_resumo)],
) -> dict[str, Any]:
    """ "Útil / não útil" de quem atende sobre o resumo (evento ResumoAvaliado)."""
    try:
        evento = await avaliar.executar(
            *identificar(lead_id),
            AvaliacaoResumo(entrada.util, entrada.responsavel, entrada.comentario, entrada.versao),
        )
    except ResumoInexistenteError:
        raise HTTPException(404, "lead sem resumo") from None
    return {"tipo": evento.tipo.value, "payload": dict(evento.payload)}
