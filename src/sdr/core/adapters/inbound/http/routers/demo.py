"""Rotas de DEMONSTRAÇÃO (não são de produção): aceleram o tempo para mostrar o follow-up."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel

from sdr.core.adapters.inbound.http.dependencias import obter_programar_followups
from sdr.core.adapters.inbound.http.lead_publico import identificar
from sdr.core.application.use_cases.followup import OptOutError, ProgramarFollowUps

router = APIRouter(prefix="/demo", tags=["demo"])


class FollowUpResposta(BaseModel):
    id: UUID
    tipo: str
    etapa: int
    executar_em: datetime


@router.post(
    "/leads/{lead_id}/simular-inatividade",
    response_model=FollowUpResposta,
    responses={
        404: {"description": "Lead não encontrado ou sem cadência aplicável"},
        409: {"description": "Lead fez opt-out"},
    },
)
async def simular_inatividade(
    lead_id: Annotated[str, Path(min_length=1, max_length=100)],
    programar: Annotated[ProgramarFollowUps, Depends(obter_programar_followups)],
) -> FollowUpResposta:
    """Faz de conta que o lead sumiu: o próximo follow-up vence AGORA (ignora o expediente)
    e o worker o envia na próxima varredura."""
    try:
        followup = await programar.simular_inatividade(*identificar(lead_id))
    except LookupError:
        raise HTTPException(404, f"lead {lead_id!r} não encontrado") from None
    except OptOutError:
        raise HTTPException(409, "o lead fez opt-out: sem mensagens ativas") from None
    if followup is None:
        raise HTTPException(404, "nenhuma cadência de follow-up se aplica a este lead")
    return FollowUpResposta(
        id=followup.id,
        tipo=followup.tipo.value,
        etapa=followup.etapa,
        executar_em=followup.executar_em,
    )
