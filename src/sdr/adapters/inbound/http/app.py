from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager

from fastapi import FastAPI

from sdr.adapters.inbound.http.dependencias import Dependencias
from sdr.adapters.inbound.http.routers import health


def criar_app(
    dependencias: Dependencias,
    *,
    ao_encerrar: Sequence[Callable[[], Awaitable[None]]] = (),
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        for encerrar in ao_encerrar:
            await encerrar()

    app = FastAPI(title="SDR Imobiliário", version="0.1.0", lifespan=lifespan)
    app.state.dependencias = dependencias
    app.include_router(health.router)
    return app
