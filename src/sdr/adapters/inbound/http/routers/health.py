from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel

from sdr.adapters.inbound.http.dependencias import obter_verificar_saude
from sdr.application.use_cases.verificar_saude import VerificarSaude

router = APIRouter(tags=["infra"])


class ComponenteSaude(BaseModel):
    componente: str
    ok: bool
    detalhe: str | None = None


class SaudeResposta(BaseModel):
    status: Literal["ok", "degradado"]
    componentes: list[ComponenteSaude]


@router.get("/health", response_model=SaudeResposta)
async def health(
    response: Response,
    verificar_saude: Annotated[VerificarSaude, Depends(obter_verificar_saude)],
) -> SaudeResposta:
    resultado = await verificar_saude.executar()
    if not resultado.ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return SaudeResposta(
        status="ok" if resultado.ok else "degradado",
        componentes=[
            ComponenteSaude(componente=c.componente, ok=c.ok, detalhe=c.detalhe)
            for c in resultado.componentes
        ],
    )
