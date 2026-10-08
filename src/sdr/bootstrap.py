"""Composition root: único lugar que instancia a infraestrutura do core e escolhe a vertical.

Cada vertical monta os próprios adapters em `VerticalPack.montar` (o composition root
dela), recebendo daqui a infraestrutura compartilhada.
"""

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

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
from sdr.core.adapters.outbound.agent.redator_resumo import RedatorResumoLLM
from sdr.core.adapters.outbound.canais.canal_web import CanalWeb
from sdr.core.adapters.outbound.embeddings.fastembed_adapter import EmbeddingFastembed
from sdr.core.adapters.outbound.eventos.publicador_asyncio import PublicadorEventosAsyncio
from sdr.core.adapters.outbound.llm.openai_adapter import LLMOpenAI
from sdr.core.adapters.outbound.persistence.agenda_postgres import AgendaPostgres
from sdr.core.adapters.outbound.persistence.crm_postgres import CRMPostgresMock
from sdr.core.adapters.outbound.persistence.database import criar_engine, criar_fabrica_sessao
from sdr.core.adapters.outbound.persistence.repositorios_conversa_sql import (
    ConversaRepositorySql,
    LeadEventoRepositorySql,
    LeadRepositorySql,
)
from sdr.core.adapters.outbound.persistence.resumos_sql import ResumoRepositorySql
from sdr.core.adapters.outbound.persistence.trava_turno_postgres import TravaTurnoPostgres
from sdr.core.adapters.outbound.persistence.verificador_saude_postgres import (
    VerificadorSaudePostgres,
)
from sdr.core.adapters.outbound.relogio import RelogioSistema
from sdr.core.adapters.outbound.turnos.agendador_debounce import AgendadorDebounce
from sdr.core.application.ports.llm import LLMPort
from sdr.core.application.use_cases.atendimento import (
    AplicarAcaoAtendimento,
    AssumirAtendimento,
    DevolverAtendimento,
    EnviarMensagemResponsavel,
    ListarAtendimentos,
)
from sdr.core.application.use_cases.conduzir_agendamento import ConduzirAgendamento
from sdr.core.application.use_cases.consultar_agenda_e_resumo import (
    ListarAgendamentos,
    ObterResumo,
)
from sdr.core.application.use_cases.consultar_conversas import (
    ListarLeads,
    ObterHistorico,
    RecuperarTurnosPendentes,
)
from sdr.core.application.use_cases.gerar_resumo_handoff import GerarResumoHandoff
from sdr.core.application.use_cases.obter_lead import ObterLead
from sdr.core.application.use_cases.processar_turno import ProcessarTurno
from sdr.core.application.use_cases.receber_mensagem import ReceberMensagem
from sdr.core.application.use_cases.verificar_saude import VerificarSaude
from sdr.core.domain.atendimento import HorarioAtendimento
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
    obter_lead: ObterLead
    obter_resumo: ObterResumo
    listar_agendamentos: ListarAgendamentos
    publicador: PublicadorEventosAsyncio
    gerar_resumo: GerarResumoHandoff | None
    listar_atendimentos: ListarAtendimentos
    assumir_atendimento: AssumirAtendimento
    devolver_atendimento: DevolverAtendimento
    enviar_mensagem_responsavel: EnviarMensagemResponsavel
    agenda: AgendaPostgres
    relogio: RelogioSistema
    settings: Settings

    async def semear_agenda(self) -> int:
        """Mock: garante os responsáveis da vertical e a grade dos próximos dias úteis."""
        s = self.settings
        fuso = ZoneInfo(s.fuso_operacao)
        return await self.agenda.semear(
            self.vertical.responsaveis_iniciais,
            inicio=self.relogio.agora().astimezone(fuso).date(),
            dias=s.agenda_mock_dias_uteis,
            hora_inicio=s.agenda_mock_hora_inicio,
            hora_fim=s.agenda_mock_hora_fim,
            duracao_min=s.agenda_mock_duracao_min,
            fuso=fuso,
        )

    async def encerrar(self) -> None:
        await self.agendador.encerrar()
        await self.publicador.encerrar()
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
    relogio = RelogioSistema()
    fuso = ZoneInfo(settings.fuso_operacao)
    agenda = AgendaPostgres(sessoes)
    horario = HorarioAtendimento.de_texto(
        settings.atendimento_dias, settings.atendimento_faixas, settings.fuso_operacao
    )
    conduzir_agendamento = None
    if vertical.tipos_agendamento and vertical.regra_atribuicao is not None:
        conduzir_agendamento = ConduzirAgendamento(
            agenda,
            vertical.regra_atribuicao,
            vertical.tipos_agendamento,
            relogio,
            fuso=fuso,
            antecedencia=timedelta(hours=settings.agenda_antecedencia_horas),
            janela=timedelta(days=settings.agenda_janela_dias),
            sugestoes=settings.agenda_sugestoes,
        )
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
        fuso=fuso,
        agenda=conduzir_agendamento,
        relogio=relogio,
        horario=horario,
    )

    # Reações fora do turno: resumo para o responsável + CRM (se a vertical tem template).
    publicador = PublicadorEventosAsyncio()
    resumos = ResumoRepositorySql(sessoes)
    gerar_resumo = None
    if vertical.template_resumo is not None:
        gerar_resumo = GerarResumoHandoff(
            leads,
            conversas,
            eventos,
            resumos=resumos,
            redator=RedatorResumoLLM(
                criar_llm(settings, settings.llm_model_summary or settings.llm_modelo), fuso
            ),
            crm=CRMPostgresMock(
                sessoes, Path(settings.crm_mock_log) if settings.crm_mock_log else None
            ),
            agenda=agenda,
            relogio=relogio,
            template=vertical.template_resumo,
            intencoes=vertical.intencoes,
        )
        publicador.assinar(gerar_resumo.ao_publicar)

    canais = {Canal.WEB: CanalWeb()}
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
        canais=canais,
        janela_historico=settings.conversa_janela_historico,
        publicador=publicador,
        atendimento=AplicarAcaoAtendimento.com(leads, eventos, relogio, publicador),
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
        obter_lead=ObterLead(leads, eventos, vertical.intencoes, agenda, relogio),
        obter_resumo=ObterResumo(leads, resumos),
        listar_agendamentos=ListarAgendamentos(agenda, leads),
        publicador=publicador,
        gerar_resumo=gerar_resumo,
        listar_atendimentos=ListarAtendimentos(leads, relogio),
        assumir_atendimento=AssumirAtendimento(leads, eventos, relogio, publicador),
        devolver_atendimento=DevolverAtendimento(leads, eventos, relogio, publicador),
        enviar_mensagem_responsavel=EnviarMensagemResponsavel(leads, conversas, canais, relogio),
        agenda=agenda,
        relogio=relogio,
        settings=settings,
    )


def criar_aplicacao(settings: Settings | None = None) -> FastAPI:
    settings = settings or obter_settings()
    logging.basicConfig(level=settings.log_level)
    container = montar_container(settings)

    async def aquecer_embedding() -> None:
        await asyncio.to_thread(container.embedding.carregar)

    async def semear_agenda() -> None:
        if criados := await container.semear_agenda():
            logging.getLogger(__name__).info("Agenda mock: %d slot(s) novo(s)", criados)

    async def recuperar_turnos() -> None:
        if total := await container.recuperar_turnos.executar():
            logging.getLogger(__name__).info("%d turno(s) pendente(s) reagendado(s)", total)

    return criar_app(
        Dependencias(
            verificar_saude=lambda: container.verificar_saude,
            receber_mensagem=lambda: container.receber_mensagem,
            obter_historico=lambda: container.obter_historico,
            listar_leads=lambda: container.listar_leads,
            obter_lead=lambda: container.obter_lead,
            obter_resumo=lambda: container.obter_resumo,
            listar_agendamentos=lambda: container.listar_agendamentos,
            listar_atendimentos=lambda: container.listar_atendimentos,
            assumir_atendimento=lambda: container.assumir_atendimento,
            devolver_atendimento=lambda: container.devolver_atendimento,
            enviar_mensagem_responsavel=lambda: container.enviar_mensagem_responsavel,
        ),
        routers=container.vertical.routers,
        ao_iniciar=[
            *([aquecer_embedding] if settings.embedding_carregar_no_inicio else []),
            semear_agenda,
            recuperar_turnos,
        ],
        ao_encerrar=[container.encerrar],
    )
