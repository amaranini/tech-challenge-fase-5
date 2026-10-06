"""Composition root: único lugar que instancia adapters e injeta dependências."""

import logging

from fastapi import FastAPI

from sdr.adapters.inbound.http.app import criar_app
from sdr.adapters.inbound.http.dependencias import Dependencias
from sdr.adapters.outbound.persistence.database import criar_engine
from sdr.adapters.outbound.persistence.verificador_saude_postgres import VerificadorSaudePostgres
from sdr.application.use_cases.verificar_saude import VerificarSaude
from sdr.config.settings import Settings, obter_settings


def criar_aplicacao(settings: Settings | None = None) -> FastAPI:
    settings = settings or obter_settings()
    logging.basicConfig(level=settings.log_level)

    engine = criar_engine(settings.database_url)
    verificadores = [VerificadorSaudePostgres(engine)]

    dependencias = Dependencias(
        verificar_saude=lambda: VerificarSaude(verificadores),
    )
    return criar_app(dependencias, ao_encerrar=[engine.dispose])
