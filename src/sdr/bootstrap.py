"""Composition root: único lugar que instancia a infraestrutura do core e escolhe a vertical.

Cada vertical monta os próprios adapters em `VerticalPack.montar` (o composition root
dela), recebendo daqui a infraestrutura compartilhada.
"""

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine

from sdr.config.settings import Settings, obter_settings
from sdr.core.adapters.inbound.http.app import criar_app
from sdr.core.adapters.inbound.http.dependencias import Dependencias
from sdr.core.adapters.outbound.embeddings.fastembed_adapter import EmbeddingFastembed
from sdr.core.adapters.outbound.persistence.database import criar_engine, criar_fabrica_sessao
from sdr.core.adapters.outbound.persistence.verificador_saude_postgres import (
    VerificadorSaudePostgres,
)
from sdr.core.application.use_cases.verificar_saude import VerificarSaude
from sdr.core.vertical import InfraCompartilhada, VerticalMontada, VerticalPack
from sdr.verticals.imobiliario.pack import PackImobiliario

# Registro de verticais disponíveis; a ativa vem de VERTICAL no .env.
VERTICAIS: dict[str, Callable[[], VerticalPack]] = {
    "imobiliario": PackImobiliario,
}


@dataclass
class Container:
    engine: AsyncEngine
    embedding: EmbeddingFastembed
    verificar_saude: VerificarSaude
    vertical: VerticalMontada

    async def encerrar(self) -> None:
        await self.engine.dispose()


def montar_container(settings: Settings | None = None) -> Container:
    settings = settings or obter_settings()
    try:
        criar_pack = VERTICAIS[settings.vertical]
    except KeyError:
        raise RuntimeError(
            f"VERTICAL={settings.vertical!r} desconhecida; disponíveis: {sorted(VERTICAIS)}"
        ) from None

    engine = criar_engine(settings.database_url)
    embedding = EmbeddingFastembed(
        settings.embedding_modelo, settings.embedding_dimensao, settings.embedding_cache_dir
    )
    infra = InfraCompartilhada(sessoes=criar_fabrica_sessao(engine), embedding=embedding)

    return Container(
        engine=engine,
        embedding=embedding,
        verificar_saude=VerificarSaude([VerificadorSaudePostgres(engine)]),
        vertical=criar_pack().montar(infra),
    )


def criar_aplicacao(settings: Settings | None = None) -> FastAPI:
    settings = settings or obter_settings()
    logging.basicConfig(level=settings.log_level)
    container = montar_container(settings)

    async def aquecer_embedding() -> None:
        await asyncio.to_thread(container.embedding.carregar)

    return criar_app(
        Dependencias(verificar_saude=lambda: container.verificar_saude),
        routers=container.vertical.routers,
        ao_iniciar=[aquecer_embedding] if settings.embedding_carregar_no_inicio else [],
        ao_encerrar=[container.encerrar],
    )
