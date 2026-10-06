"""Composition root: único lugar que instancia adapters e injeta dependências."""

import asyncio
import logging
from dataclasses import dataclass

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine

from sdr.adapters.inbound.http.app import criar_app
from sdr.adapters.inbound.http.dependencias import Dependencias
from sdr.adapters.outbound.embeddings.fastembed_adapter import EmbeddingFastembed
from sdr.adapters.outbound.interpretacao.interpretador_regras import InterpretadorRegras
from sdr.adapters.outbound.persistence.database import criar_engine, criar_fabrica_sessao
from sdr.adapters.outbound.persistence.imovel_repository_sql import ImovelRepositorySql
from sdr.adapters.outbound.persistence.modelos import DIMENSAO_EMBEDDING
from sdr.adapters.outbound.persistence.verificador_saude_postgres import VerificadorSaudePostgres
from sdr.adapters.outbound.vector.busca_imoveis_pgvector import BuscaImoveisPgvector
from sdr.application.use_cases.buscar_imoveis import BuscarImoveis
from sdr.application.use_cases.cadastrar_imoveis import CadastrarImoveis
from sdr.application.use_cases.verificar_saude import VerificarSaude
from sdr.config.settings import Settings, obter_settings


@dataclass
class Container:
    engine: AsyncEngine
    embedding: EmbeddingFastembed
    verificar_saude: VerificarSaude
    buscar_imoveis: BuscarImoveis
    cadastrar_imoveis: CadastrarImoveis

    async def encerrar(self) -> None:
        await self.engine.dispose()


def montar_container(settings: Settings | None = None) -> Container:
    settings = settings or obter_settings()
    if settings.embedding_dimensao != DIMENSAO_EMBEDDING:
        raise RuntimeError(
            f"EMBEDDING_DIMENSAO={settings.embedding_dimensao}, mas a coluna vetorial tem "
            f"{DIMENSAO_EMBEDDING}: crie uma migration e reindexe antes de trocar o modelo."
        )

    engine = criar_engine(settings.database_url)
    sessoes = criar_fabrica_sessao(engine)

    embedding = EmbeddingFastembed(
        settings.embedding_modelo, settings.embedding_dimensao, settings.embedding_cache_dir
    )
    repositorio_imoveis = ImovelRepositorySql(sessoes)
    busca_imoveis = BuscaImoveisPgvector(sessoes)
    interpretador = InterpretadorRegras(settings.busca_distancia_metro_padrao_m)

    return Container(
        engine=engine,
        embedding=embedding,
        verificar_saude=VerificarSaude([VerificadorSaudePostgres(engine)]),
        buscar_imoveis=BuscarImoveis(busca_imoveis, embedding, interpretador),
        cadastrar_imoveis=CadastrarImoveis(repositorio_imoveis, busca_imoveis, embedding),
    )


def criar_aplicacao(settings: Settings | None = None) -> FastAPI:
    settings = settings or obter_settings()
    logging.basicConfig(level=settings.log_level)
    container = montar_container(settings)

    async def aquecer_embedding() -> None:
        await asyncio.to_thread(container.embedding.carregar)

    dependencias = Dependencias(
        verificar_saude=lambda: container.verificar_saude,
        buscar_imoveis=lambda: container.buscar_imoveis,
    )
    return criar_app(
        dependencias,
        ao_iniciar=[aquecer_embedding] if settings.embedding_carregar_no_inicio else [],
        ao_encerrar=[container.encerrar],
    )
