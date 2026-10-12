from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI, Request

from sdr.core.adapters.inbound.http.dependencias import Dependencias
from sdr.core.adapters.inbound.http.protecao import (
    exigir_admin,
    ip_cliente,
    limitar,
    obter_protecao,
)
from sdr.core.adapters.inbound.http.routers import (
    agendamentos,
    atendimentos,
    conversas,
    demo,
    health,
    leads,
    lgpd,
    painel,
    whatsapp_meta,
    whatsapp_twilio,
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

    producao = dependencias.protecao.producao
    # Em produção: sem Swagger público e sem as rotas de demonstração (ADR 015).
    app = FastAPI(
        title="SDR Conversacional",
        version="0.1.0",
        lifespan=lifespan,
        docs_url=None if producao else "/docs",
        redoc_url=None if producao else "/redoc",
        openapi_url=None if producao else "/openapi.json",
    )
    app.state.dependencias = dependencias
    admin = [Depends(exigir_admin)]
    app.include_router(health.router)
    app.include_router(conversas.router)  # público (chat web): rate limit nas rotas
    app.include_router(leads.router, dependencies=admin)
    app.include_router(agendamentos.router, dependencies=admin)
    app.include_router(atendimentos.router, dependencies=admin)
    app.include_router(painel.router, dependencies=admin)
    app.include_router(lgpd.router, dependencies=admin)
    if not producao:
        app.include_router(demo.router, dependencies=admin)
    app.include_router(whatsapp_twilio.router)  # assinatura do provedor + rate limit
    app.include_router(whatsapp_meta.router)
    for router in routers:  # da vertical (ex.: catálogo público): rate limit por IP
        app.include_router(router, dependencies=[Depends(_limitar_ip)])
    return app


async def _limitar_ip(request: Request) -> None:
    protecao = obter_protecao(request)
    await limitar(request, f"ip:{ip_cliente(request)}", protecao.limite_ip_por_minuto)
