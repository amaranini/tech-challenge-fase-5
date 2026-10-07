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
from sdr.core.adapters.outbound.agent.agente_qualificador import (
    AgenteQualificador,
    ConfigQualificacao,
    LLMsPorNo,
)
from sdr.core.adapters.outbound.canais.canal_web import CanalWeb
from sdr.core.adapters.outbound.embeddings.fastembed_adapter import EmbeddingFastembed
from sdr.core.adapters.outbound.llm.openai_adapter import LLMOpenAI
from sdr.core.adapters.outbound.persistence.database import criar_engine, criar_fabrica_sessao
from sdr.core.adapters.outbound.persistence.repositorios_conversa_sql import (
    ConversaRepositorySql,
    LeadEventoRepositorySql,
    LeadRepositorySql,
)
from sdr.core.adapters.outbound.persistence.trava_turno_postgres import TravaTurnoPostgres
from sdr.core.adapters.outbound.persistence.verificador_saude_postgres import (
    VerificadorSaudePostgres,
)
from sdr.core.adapters.outbound.turnos.agendador_debounce import AgendadorDebounce
from sdr.core.application.ports.llm import LLMPort
from sdr.core.application.use_cases.consultar_conversas import (
    ListarLeads,
    ObterHistorico,
    RecuperarTurnosPendentes,
)
from sdr.core.application.use_cases.processar_turno import ProcessarTurno
from sdr.core.application.use_cases.receber_mensagem import ReceberMensagem
from sdr.core.application.use_cases.verificar_saude import VerificarSaude
from sdr.core.domain.conversa import Canal
from sdr.core.vertical import InfraCompartilhada, VerticalMontada, VerticalPack
from sdr.verticals.imobiliario.pack import PackImobiliario

# Registro de verticais disponíveis; a ativa vem de VERTICAL no .env.
VERTICAIS: dict[str, Callable[[], VerticalPack]] = {
    "imobiliario": PackImobiliario,
}


def _llm_openai(settings: Settings, modelo: str) -> LLMPort:
    chave = settings.openai_api_key.get_secret_value() if settings.openai_api_key else None
    return LLMOpenAI(
        api_key=chave or None,
        modelo=modelo,
        temperatura=settings.llm_temperatura,
        timeout_s=settings.llm_timeout_s,
    )


# Provedores de LLM disponíveis; o ativo vem de LLM_PROVIDER no .env.
PROVEDORES_LLM: dict[str, Callable[[Settings, str], LLMPort]] = {
    "openai": _llm_openai,
}


@dataclass
class Container:
    engine: AsyncEngine
    embedding: EmbeddingFastembed
    verificar_saude: VerificarSaude
    vertical: VerticalMontada
    eventos: LeadEventoRepositorySql
    agendador: AgendadorDebounce
    receber_mensagem: ReceberMensagem
    processar_turno: ProcessarTurno
    recuperar_turnos: RecuperarTurnosPendentes
    obter_historico: ObterHistorico
    listar_leads: ListarLeads

    async def encerrar(self) -> None:
        await self.agendador.encerrar()
        await self.engine.dispose()


def montar_container(settings: Settings | None = None) -> Container:
    settings = settings or obter_settings()
    try:
        criar_pack = VERTICAIS[settings.vertical]
    except KeyError:
        raise RuntimeError(
            f"VERTICAL={settings.vertical!r} desconhecida; disponíveis: {sorted(VERTICAIS)}"
        ) from None

    try:
        criar_llm = PROVEDORES_LLM[settings.llm_provider]
    except KeyError:
        raise RuntimeError(
            f"LLM_PROVIDER={settings.llm_provider!r} desconhecido; "
            f"disponíveis: {sorted(PROVEDORES_LLM)}"
        ) from None

    engine = criar_engine(settings.database_url)
    embedding = EmbeddingFastembed(
        settings.embedding_modelo, settings.embedding_dimensao, settings.embedding_cache_dir
    )
    sessoes = criar_fabrica_sessao(engine)
    vertical = criar_pack().montar(InfraCompartilhada(sessoes=sessoes, embedding=embedding))

    leads, conversas = LeadRepositorySql(sessoes), ConversaRepositorySql(sessoes)
    eventos = LeadEventoRepositorySql(sessoes)
    llms = LLMsPorNo(
        roteador=criar_llm(settings, settings.llm_model_router or settings.llm_modelo),
        extracao=criar_llm(settings, settings.llm_model_extraction or settings.llm_modelo),
        agente=criar_llm(settings, settings.llm_model_agent or settings.llm_modelo),
    )
    agente = AgenteQualificador(
        llms,
        vertical.persona,
        ConfigQualificacao(
            intencoes=vertical.intencoes,
            regras=vertical.regras_qualificacao,
            prompt_descoberta=vertical.prompt_descoberta,
            limiar_confianca=settings.router_confianca_min,
            janela_extracao=settings.extracao_janela_mensagens,
        ),
        ferramentas=vertical.ferramentas,
        max_passos=settings.agente_max_passos,
    )

    agendador = AgendadorDebounce(settings.debounce_segundos, settings.debounce_max_segundos)
    processar_turno = ProcessarTurno(
        leads,
        conversas,
        agente,
        vertical.catalogo,
        eventos=eventos,
        persona=vertical.persona,
        trava=TravaTurnoPostgres(engine),
        agendador=agendador,
        canais={Canal.WEB: CanalWeb()},
        janela_historico=settings.conversa_janela_historico,
    )
    agendador.definir_executor(processar_turno.executar)

    return Container(
        engine=engine,
        embedding=embedding,
        verificar_saude=VerificarSaude([VerificadorSaudePostgres(engine)]),
        vertical=vertical,
        eventos=eventos,
        agendador=agendador,
        receber_mensagem=ReceberMensagem(leads, conversas, eventos, agendador),
        processar_turno=processar_turno,
        recuperar_turnos=RecuperarTurnosPendentes(conversas, agendador),
        obter_historico=ObterHistorico(leads, conversas),
        listar_leads=ListarLeads(leads),
    )


def criar_aplicacao(settings: Settings | None = None) -> FastAPI:
    settings = settings or obter_settings()
    logging.basicConfig(level=settings.log_level)
    container = montar_container(settings)

    async def aquecer_embedding() -> None:
        await asyncio.to_thread(container.embedding.carregar)

    async def recuperar_turnos() -> None:
        if total := await container.recuperar_turnos.executar():
            logging.getLogger(__name__).info("%d turno(s) pendente(s) reagendado(s)", total)

    return criar_app(
        Dependencias(
            verificar_saude=lambda: container.verificar_saude,
            receber_mensagem=lambda: container.receber_mensagem,
            obter_historico=lambda: container.obter_historico,
            listar_leads=lambda: container.listar_leads,
        ),
        routers=container.vertical.routers,
        ao_iniciar=[
            *([aquecer_embedding] if settings.embedding_carregar_no_inicio else []),
            recuperar_turnos,
        ],
        ao_encerrar=[container.encerrar],
    )
