from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI

from sdr.core.adapters.inbound.http.dependencias import Dependencias
from sdr.core.adapters.inbound.http.routers import (
    agendamentos,
    atendimentos,
    conversas,
    health,
    leads,
)


def criar_app(
    dependencias: Dependencias,
    *,
    routers: Sequence[APIRouter] = (),
    ao_iniciar: Sequence[Callable[[], Awaitable[None]]] = (),
    ao_encerrar: Sequence[Callable[[], Awaitable[None]]] = (),
) -> FastAPI:
    """`routers`: rotas próprias da vertical ativa (registradas pelo VerticalPack)."""

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        for iniciar in ao_iniciar:
            await iniciar()
        yield
        for encerrar in ao_encerrar:
            await encerrar()

    app = FastAPI(title="SDR Conversacional", version="0.1.0", lifespan=lifespan)
    app.state.dependencias = dependencias
    app.include_router(health.router)
    app.include_router(conversas.router)
    app.include_router(leads.router)
    app.include_router(agendamentos.router)
    app.include_router(atendimentos.router)
    for router in routers:
        app.include_router(router)
    return app
