"""Direitos do titular (LGPD), só com credencial de admin: exportar e anonimizar um lead."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from pydantic import BaseModel

from sdr.core.adapters.inbound.http.dependencias import obter_dependencias
from sdr.core.adapters.inbound.http.lead_publico import identificar
from sdr.core.application.use_cases.lgpd import (
    AnonimizarLead,
    ExportarDadosLead,
    LeadJaAnonimizadoError,
    LeadNaoEncontradoError,
)

router = APIRouter(prefix="/lgpd", tags=["lgpd"])

LeadIdPath = Annotated[str, Path(min_length=1, max_length=100)]
NAO_ENCONTRADO: dict[int | str, dict[str, Any]] = {404: {"description": "Lead não encontrado"}}


def obter_exportar(request: Request) -> ExportarDadosLead:
    caso = obter_dependencias(request).exportar_dados_lead()
    if caso is None:
        raise HTTPException(404, "LGPD não configurado")
    return caso


def obter_anonimizar(request: Request) -> AnonimizarLead:
    caso = obter_dependencias(request).anonimizar_lead()
    if caso is None:
        raise HTTPException(404, "LGPD não configurado")
    return caso


class AnonimizacaoResposta(BaseModel):
    lead_id: str
    remetente_anonimo: str
    registros: dict[str, int]


@router.get("/leads/{lead_id}/exportacao", responses=NAO_ENCONTRADO)
async def exportar(
    lead_id: LeadIdPath, caso: Annotated[ExportarDadosLead, Depends(obter_exportar)]
) -> dict[str, Any]:
    """Tudo o que o sistema guarda sobre o titular (JSON), para atender o pedido de acesso."""
    try:
        return dict(await caso.executar(*identificar(lead_id)))
    except LeadNaoEncontradoError:
        raise HTTPException(404, "lead não encontrado") from None


@router.post(
    "/leads/{lead_id}/anonimizacao",
    response_model=AnonimizacaoResposta,
    responses={**NAO_ENCONTRADO, 409: {"description": "Lead já anonimizado"}},
)
async def anonimizar(
    lead_id: LeadIdPath, caso: Annotated[AnonimizarLead, Depends(obter_anonimizar)]
) -> AnonimizacaoResposta:
    """Remove os dados que identificam o titular (irreversível); métricas continuam."""
    try:
        resultado = await caso.executar(*identificar(lead_id))
    except LeadNaoEncontradoError:
        raise HTTPException(404, "lead não encontrado") from None
    except LeadJaAnonimizadoError:
        raise HTTPException(409, "lead já anonimizado") from None
    return AnonimizacaoResposta(
        lead_id=resultado.lead_id,
        remetente_anonimo=resultado.remetente_anonimo,
        registros=dict(resultado.registros),
    )
