"""Composition root: único lugar que instancia a infraestrutura do core e escolhe a vertical.

Cada vertical monta os próprios adapters em `VerticalPack.montar` (o composition root
dela), recebendo daqui a infraestrutura compartilhada.
"""

import asyncio
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import FastAPI
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from sdr.config.settings import Settings, obter_settings
from sdr.core.adapters.inbound.http.app import criar_app
from sdr.core.adapters.inbound.http.dependencias import Dependencias
from sdr.core.adapters.inbound.http.protecao import ProtecaoHttp
from sdr.core.adapters.inbound.http.routers.whatsapp_meta import WebhookMeta
from sdr.core.adapters.inbound.http.routers.whatsapp_twilio import WebhookTwilio
from sdr.core.adapters.logs import instalar_mascara_pii
from sdr.core.adapters.outbound.agent.agente_qualificador import (
    VERSOES_PROMPTS,
    AgenteQualificador,
    ConfigQualificacao,
    LLMsPorNo,
)
from sdr.core.adapters.outbound.agent.redator_mensagem_ativa import RedatorMensagemAtivaLLM
from sdr.core.adapters.outbound.agent.redator_resumo import RedatorResumoLLM
from sdr.core.adapters.outbound.canais.canal_web import CanalWeb
from sdr.core.adapters.outbound.canais.whatsapp_formato import LIMITE_TWILIO_WHATSAPP
from sdr.core.adapters.outbound.canais.whatsapp_meta import (
    LIMITE_META_TEXTO,
    CanalWhatsAppMeta,
    ConfigMeta,
    TemplateMeta,
)
from sdr.core.adapters.outbound.canais.whatsapp_twilio import (
    CanalWhatsAppTwilio,
    ConfigTwilio,
    TemplateProvedor,
)
from sdr.core.adapters.outbound.embeddings.fastembed_adapter import EmbeddingFastembed
from sdr.core.adapters.outbound.eventos.publicador_asyncio import PublicadorEventosAsyncio
from sdr.core.adapters.outbound.limite_taxa.memoria import LimitadorTaxaMemoria
from sdr.core.adapters.outbound.llm.openai_adapter import LLMOpenAI
from sdr.core.adapters.outbound.observabilidade.langfuse_rastreio import RastreioLangfuse
from sdr.core.adapters.outbound.observabilidade.llm_observado import LLMObservado
from sdr.core.adapters.outbound.persistence.agenda_postgres import AgendaPostgres
from sdr.core.adapters.outbound.persistence.consultas_painel_sql import ConsultasPainelSql
from sdr.core.adapters.outbound.persistence.consultas_qualidade_sql import (
    AvaliacoesOfflineSql,
    ConsultasQualidadeSql,
)
from sdr.core.adapters.outbound.persistence.crm_postgres import CRMPostgresMock
from sdr.core.adapters.outbound.persistence.database import criar_engine, criar_fabrica_sessao
from sdr.core.adapters.outbound.persistence.followups_sql import FollowUpRepositorySql
from sdr.core.adapters.outbound.persistence.lgpd_sql import DadosTitularSql
from sdr.core.adapters.outbound.persistence.repositorios_conversa_sql import (
    ConversaRepositorySql,
    EntregaRepositorySql,
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
from sdr.core.application.consultas.painel import ConsultarPainel
from sdr.core.application.consultas.qualidade import (
    ConsultarQualidade,
    RegistrarAvaliacaoOffline,
)
from sdr.core.application.ports.canal import CanalMensagemPort
from sdr.core.application.ports.llm import LLMPort
from sdr.core.application.ports.observabilidade import RastreioNulo, RastreioPort
from sdr.core.application.use_cases.atendimento import (
    AplicarAcaoAtendimento,
    AssumirAtendimento,
    DevolverAtendimento,
    EnviarMensagemResponsavel,
    ListarAtendimentos,
)
from sdr.core.application.use_cases.avaliar_resumo import AvaliarResumo
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
from sdr.core.application.use_cases.documentar_templates import documentar_templates
from sdr.core.application.use_cases.entregar_mensagem import (
    AtualizarStatusEntrega,
    ConfigEntrega,
    EntregarMensagem,
)
from sdr.core.application.use_cases.followup import (
    ConfigFollowUp,
    ExecutarFollowUps,
    ProgramarFollowUps,
)
from sdr.core.application.use_cases.gerar_resumo_handoff import GerarResumoHandoff
from sdr.core.application.use_cases.lgpd import AnonimizarLead, ExportarDadosLead
from sdr.core.application.use_cases.obter_lead import ObterLead
from sdr.core.application.use_cases.processar_turno import ProcessarTurno
from sdr.core.application.use_cases.receber_mensagem import ReceberMensagem
from sdr.core.application.use_cases.verificar_saude import VerificarSaude
from sdr.core.domain.atendimento import HorarioAtendimento
from sdr.core.domain.conversa import Canal
from sdr.core.domain.followup import UnidadeTempo
from sdr.core.vertical import InfraCompartilhada, VerticalMontada, VerticalPack
from sdr.verticals.imobiliario.pack import PackImobiliario

# Registro de verticais disponíveis; a ativa vem de VERTICAL no .env.
VERTICAIS: dict[str, Callable[[], VerticalPack]] = {
    "imobiliario": PackImobiliario,
}


AMBIENTE_PRODUCAO = "producao"


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

ROTA_STATUS_TWILIO = "/webhooks/whatsapp/twilio/status"
logger = logging.getLogger(__name__)

CanalWhatsApp = CanalWhatsAppTwilio | CanalWhatsAppMeta


def _rastreio(settings: Settings, vertical: VerticalMontada) -> RastreioPort:
    """Langfuse se as chaves existirem; senão, no-op (nada sai do processo)."""
    secret = _segredo(settings.langfuse_secret_key)
    if not (settings.langfuse_public_key and secret):
        logger.info("Observabilidade desligada (sem LANGFUSE_PUBLIC_KEY/SECRET_KEY)")
        return RastreioNulo()
    logger.info("Observabilidade: Langfuse em %s", settings.langfuse_host)
    return RastreioLangfuse.criar(
        public_key=settings.langfuse_public_key,
        secret_key=secret,
        host=settings.langfuse_host,
        ambiente=settings.ambiente,
        metadados_fixos={
            "vertical": settings.vertical,
            "prompt_persona": vertical.persona.versao_prompt,
            "prompts_core": VERSOES_PROMPTS,
        },
    )


def _versao_agente(settings: Settings, vertical: VerticalMontada) -> dict[str, object]:
    """O que identifica a versão em produção (página Qualidade da IA e eval offline)."""
    padrao = settings.llm_modelo
    return {
        "vertical": settings.vertical,
        "persona": vertical.persona.versao_prompt,
        "prompts_core": VERSOES_PROMPTS,
        "modelos": {
            "roteador": settings.llm_model_router or padrao,
            "extracao": settings.llm_model_extraction or padrao,
            "agente": settings.llm_model_agent or padrao,
            "resumo": settings.llm_model_summary or padrao,
        },
    }


def _segredo(valor: SecretStr | None) -> str:
    return valor.get_secret_value() if valor else ""


def _conferir_templates(mapeados: Mapping[str, tuple[str, ...]], vertical: VerticalMontada) -> None:
    """Mapeamento da operação × templates lógicos declarados pela vertical."""
    declarados = {t.nome: t for t in vertical.templates}
    for nome, variaveis in mapeados.items():
        if nome not in declarados:
            logger.warning("WHATSAPP_TEMPLATES: %r não é template lógico da vertical", nome)
        elif variaveis and set(variaveis) != set(declarados[nome].ordem):
            logger.warning(
                "WHATSAPP_TEMPLATES: %r mapeia %s, mas o template lógico tem %s",
                nome,
                list(variaveis),
                list(declarados[nome].ordem),
            )
    if faltando := sorted(set(declarados) - set(mapeados)):
        logger.info("Templates sem mapeamento (fora da janela não serão enviados): %s", faltando)


def _whatsapp_twilio(settings: Settings) -> CanalWhatsAppTwilio:
    token = _segredo(settings.twilio_auth_token)
    if not (settings.twilio_account_sid and token and settings.public_base_url):
        raise RuntimeError(
            "WhatsApp (Twilio) exige TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN e PUBLIC_BASE_URL"
        )
    templates = {
        nome: TemplateProvedor(
            content_sid=str(cfg["content_sid"]),
            idioma=str(cfg.get("idioma", "pt_BR")),
            variaveis=tuple(cfg.get("variaveis") or ()),
        )
        for nome, cfg in settings.whatsapp_templates.items()
    }
    return CanalWhatsAppTwilio(
        ConfigTwilio(
            account_sid=settings.twilio_account_sid,
            auth_token=token,
            remetente=settings.twilio_whatsapp_from,
            status_callback_url=settings.public_base_url.rstrip("/") + ROTA_STATUS_TWILIO,
            limite_caracteres=settings.whatsapp_limite_caracteres or LIMITE_TWILIO_WHATSAPP,
            templates=templates,
        )
    )


def _whatsapp_meta(settings: Settings) -> CanalWhatsAppMeta:
    if not (
        _segredo(settings.meta_access_token)
        and settings.meta_phone_number_id
        and _segredo(settings.meta_app_secret)
        and _segredo(settings.meta_verify_token)
    ):
        raise RuntimeError(
            "WhatsApp (Meta) exige META_ACCESS_TOKEN, META_PHONE_NUMBER_ID, META_APP_SECRET "
            "e META_VERIFY_TOKEN"
        )
    templates = {
        nome: TemplateMeta(
            nome=str(cfg.get("nome") or nome),
            idioma=str(cfg.get("idioma", "pt_BR")),
            variaveis=tuple(cfg.get("variaveis") or ()),
        )
        for nome, cfg in settings.whatsapp_templates.items()
    }
    return CanalWhatsAppMeta(
        ConfigMeta(
            access_token=_segredo(settings.meta_access_token),
            phone_number_id=settings.meta_phone_number_id,
            versao_graph=settings.meta_versao_graph,
            limite_caracteres=settings.whatsapp_limite_caracteres or LIMITE_META_TEXTO,
            templates=templates,
        )
    )


PROVEDORES_WHATSAPP: dict[str, Callable[[Settings], CanalWhatsApp]] = {
    "twilio": _whatsapp_twilio,
    "meta": _whatsapp_meta,
}


def _canal_whatsapp(settings: Settings, vertical: VerticalMontada) -> CanalWhatsApp | None:
    """Canal WhatsApp do provedor em WHATSAPP_PROVEDOR (vazio = desligado)."""
    provedor = (settings.whatsapp_provedor or "").strip().lower()
    if not provedor:
        return None
    try:
        criar = PROVEDORES_WHATSAPP[provedor]
    except KeyError:
        raise RuntimeError(
            f"WHATSAPP_PROVEDOR={provedor!r} desconhecido; "
            f"disponíveis: {sorted(PROVEDORES_WHATSAPP)}"
        ) from None
    _conferir_templates(
        {n: tuple(c.get("variaveis") or ()) for n, c in settings.whatsapp_templates.items()},
        vertical,
    )
    return criar(settings)


def _webhooks_whatsapp(
    settings: Settings,
    canal: CanalWhatsApp | None,
    receber: ReceberMensagem,
    atualizar: AtualizarStatusEntrega,
) -> tuple[WebhookTwilio | None, WebhookMeta | None]:
    if isinstance(canal, CanalWhatsAppTwilio):
        return (
            WebhookTwilio(
                auth_token=_segredo(settings.twilio_auth_token),
                url_base_publica=settings.public_base_url or "",
                receber_mensagem=receber,
                atualizar_entrega=atualizar,
                validar_assinatura=settings.twilio_validar_assinatura,
            ),
            None,
        )
    if isinstance(canal, CanalWhatsAppMeta):
        return None, WebhookMeta(
            app_secret=_segredo(settings.meta_app_secret),
            verify_token=_segredo(settings.meta_verify_token),
            receber_mensagem=receber,
            atualizar_entrega=atualizar,
            validar_assinatura=settings.meta_validar_assinatura,
        )
    return None, None


def _montar_saida(
    settings: Settings,
    vertical: VerticalMontada,
    sessoes: async_sessionmaker[AsyncSession],
    *,
    conversas: ConversaRepositorySql,
    eventos: LeadEventoRepositorySql,
    relogio: RelogioSistema,
) -> tuple[EntregarMensagem, AtualizarStatusEntrega, CanalWhatsApp | None]:
    """Saída: cada canal tem seu adapter; a decisão texto livre × template é do core."""
    fuso = ZoneInfo(settings.fuso_operacao)
    canais: dict[Canal, CanalMensagemPort] = {Canal.WEB: CanalWeb()}
    whatsapp = _canal_whatsapp(settings, vertical)
    if whatsapp is not None:
        canais[Canal.WHATSAPP] = whatsapp
    entregas = EntregaRepositorySql(sessoes)
    entrega = EntregarMensagem(
        conversas,
        entregas,
        eventos,
        canais,
        relogio=relogio,
        config=ConfigEntrega(
            fuso=fuso,
            janela_conversa=timedelta(hours=settings.janela_conversa_horas),
            max_caracteres_variavel=settings.template_variavel_max_caracteres,
        ),
    )
    atualizar_entrega = AtualizarStatusEntrega(entregas, eventos, relogio)
    return entrega, atualizar_entrega, whatsapp


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
    programar_followups: ProgramarFollowUps | None
    executar_followups: ExecutarFollowUps | None
    followups: FollowUpRepositorySql
    entrega: EntregarMensagem
    atualizar_entrega: AtualizarStatusEntrega
    webhook_twilio: WebhookTwilio | None
    webhook_meta: WebhookMeta | None
    rastreio: RastreioPort
    consultar_painel: ConsultarPainel
    consultar_qualidade: ConsultarQualidade
    registrar_avaliacao_offline: RegistrarAvaliacaoOffline
    avaliar_resumo: AvaliarResumo
    versao_agente: dict[str, object]
    whatsapp: CanalWhatsApp | None
    agenda: AgendaPostgres
    relogio: RelogioSistema
    exportar_dados_lead: ExportarDadosLead
    anonimizar_lead: AnonimizarLead
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

    async def preparar_rastreio(self) -> None:
        """Link dos traces (uma chamada HTTP ao Langfuse; fora do event loop)."""
        if isinstance(self.rastreio, RastreioLangfuse):
            await asyncio.to_thread(self.rastreio.preparar)

    async def encerrar(self) -> None:
        await asyncio.to_thread(self.rastreio.encerrar)
        if self.whatsapp is not None:
            await self.whatsapp.fechar()
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
    rastreio = _rastreio(settings, vertical)

    def llm_do_no(nome: str, modelo: str | None) -> LLMPort:
        """LLM de um nó, observado: cada chamada vira uma geração no rastro."""
        return LLMObservado(criar_llm(settings, modelo or settings.llm_modelo), rastreio, nome)

    llms = LLMsPorNo(
        roteador=llm_do_no("roteador", settings.llm_model_router),
        extracao=llm_do_no("extracao", settings.llm_model_extraction),
        agente=llm_do_no("agente", settings.llm_model_agent),
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
        rastreio=rastreio,
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
            redator=RedatorResumoLLM(llm_do_no("resumo", settings.llm_model_summary), fuso),
            crm=CRMPostgresMock(
                sessoes, Path(settings.crm_mock_log) if settings.crm_mock_log else None
            ),
            agenda=agenda,
            relogio=relogio,
            template=vertical.template_resumo,
            intencoes=vertical.intencoes,
            rastreio=rastreio,
        )
        publicador.assinar(gerar_resumo.ao_publicar)

    entrega, atualizar_entrega, whatsapp = _montar_saida(
        settings, vertical, sessoes, conversas=conversas, eventos=eventos, relogio=relogio
    )

    # Follow-up: programado na conversa, executado pelo worker (python -m sdr.worker).
    followups = FollowUpRepositorySql(sessoes)
    programar_followups = executar_followups = None
    if vertical.cadencias_followup:
        config_followup = ConfigFollowUp(
            cadencias=vertical.cadencias_followup,
            unidade=UnidadeTempo(settings.followup_unidade),
            horario=horario,
            respeitar_horario=settings.followup_respeitar_horario,
            sla_handoff=timedelta(minutes=settings.handoff_sla_minutos),
            lembrete=vertical.lembrete_agendamento,
            lembrete_antes=timedelta(hours=settings.followup_lembrete_horas),
        )
        programar_followups = ProgramarFollowUps(
            followups,
            leads,
            conversas,
            eventos=eventos,
            agenda=agenda,
            relogio=relogio,
            config=config_followup,
        )
        publicador.assinar(programar_followups.ao_publicar)
        executar_followups = ExecutarFollowUps(
            followups,
            programar_followups,
            leads,
            conversas=conversas,
            eventos=eventos,
            agenda=agenda,
            catalogo=vertical.catalogo,
            redator=RedatorMensagemAtivaLLM(
                llm_do_no("followup", settings.llm_model_agent), vertical.persona, fuso
            ),
            entrega=entrega,
            relogio=relogio,
            persona=vertical.persona,
            config=config_followup,
            consulta_para_ficha=vertical.consulta_followup,
            rastreio=rastreio,
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
        entrega=entrega,
        janela_historico=settings.conversa_janela_historico,
        publicador=publicador,
        atendimento=AplicarAcaoAtendimento.com(leads, eventos, relogio, publicador),
        followups=programar_followups,
        rastreio=rastreio,
    )
    agendador.definir_executor(processar_turno.executar)
    receber_mensagem = ReceberMensagem(
        leads,
        conversas,
        eventos,
        agendador,
        followups=programar_followups,
        limite_texto=settings.mensagem_max_caracteres,
    )
    webhook_twilio, webhook_meta = _webhooks_whatsapp(
        settings, whatsapp, receber_mensagem, atualizar_entrega
    )

    return Container(
        engine=engine,
        embedding=embedding,
        verificar_saude=VerificarSaude([VerificadorSaudePostgres(engine)]),
        vertical=vertical,
        eventos=eventos,
        agendador=agendador,
        receber_mensagem=receber_mensagem,
        processar_turno=processar_turno,
        recuperar_turnos=RecuperarTurnosPendentes(conversas, agendador),
        obter_historico=ObterHistorico(leads, conversas),
        listar_leads=ListarLeads(leads),
        obter_lead=ObterLead(
            leads,
            eventos,
            vertical.intencoes,
            agenda=agenda,
            relogio=relogio,
            followups=followups,
        ),
        obter_resumo=ObterResumo(leads, resumos),
        listar_agendamentos=ListarAgendamentos(agenda, leads),
        publicador=publicador,
        gerar_resumo=gerar_resumo,
        listar_atendimentos=ListarAtendimentos(leads, relogio, eventos),
        assumir_atendimento=AssumirAtendimento(leads, eventos, relogio, publicador),
        devolver_atendimento=DevolverAtendimento(leads, eventos, relogio, publicador),
        enviar_mensagem_responsavel=EnviarMensagemResponsavel(
            leads, conversas, entrega, vertical.template_resposta_responsavel
        ),
        programar_followups=programar_followups,
        executar_followups=executar_followups,
        followups=followups,
        entrega=entrega,
        atualizar_entrega=atualizar_entrega,
        webhook_twilio=webhook_twilio,
        webhook_meta=webhook_meta,
        rastreio=rastreio,
        consultar_painel=ConsultarPainel(ConsultasPainelSql(sessoes)),
        consultar_qualidade=ConsultarQualidade(
            ConsultasQualidadeSql(sessoes),
            AvaliacoesOfflineSql(sessoes),
            settings.custo_llm_por_milhao,
        ),
        registrar_avaliacao_offline=RegistrarAvaliacaoOffline(AvaliacoesOfflineSql(sessoes)),
        avaliar_resumo=AvaliarResumo(leads, resumos, eventos, relogio),
        versao_agente=_versao_agente(settings, vertical),
        whatsapp=whatsapp,
        agenda=agenda,
        relogio=relogio,
        exportar_dados_lead=ExportarDadosLead(leads, DadosTitularSql(sessoes)),
        anonimizar_lead=AnonimizarLead(
            leads, DadosTitularSql(sessoes), eventos, relogio, agenda=agenda, followups=followups
        ),
        settings=settings,
    )


def _protecao(settings: Settings) -> ProtecaoHttp:
    """Chaves de admin e rate limit (ADR 015). Em produção, sem chave a API não sobe."""
    chaves = tuple(c.strip() for c in _segredo(settings.admin_api_keys).split(",") if c.strip())
    producao = settings.ambiente == AMBIENTE_PRODUCAO
    if producao and not chaves:
        raise RuntimeError("AMBIENTE=producao exige ADMIN_API_KEYS (rotas administrativas)")
    if not chaves:
        logging.getLogger(__name__).warning(
            "ADMIN_API_KEYS vazio: rotas administrativas ABERTAS (só para ambiente local)"
        )
    leitura = tuple(c.strip() for c in _segredo(settings.leitura_api_keys).split(",") if c.strip())
    return ProtecaoHttp(
        chaves_admin=chaves,
        chaves_leitura=leitura,
        producao=producao,
        confiar_proxy=settings.confiar_proxy,
        limitador=LimitadorTaxaMemoria(),
        limite_remetente_por_minuto=settings.limite_remetente_por_minuto,
        limite_ip_por_minuto=settings.limite_ip_por_minuto,
        limite_ip_webhook_por_minuto=settings.limite_ip_webhook_por_minuto,
    )


def criar_aplicacao(settings: Settings | None = None) -> FastAPI:
    settings = settings or obter_settings()
    logging.basicConfig(level=settings.log_level)
    instalar_mascara_pii()
    protecao = _protecao(settings)
    container = montar_container(settings)

    async def aquecer_embedding() -> None:
        await asyncio.to_thread(container.embedding.carregar)

    async def semear_agenda() -> None:
        if criados := await container.semear_agenda():
            logging.getLogger(__name__).info("Agenda mock: %d slot(s) novo(s)", criados)

    async def preparar_rastreio() -> None:
        await container.preparar_rastreio()

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
            programar_followups=lambda: container.programar_followups,
            consultar_painel=lambda: container.consultar_painel,
            consultar_qualidade=lambda: container.consultar_qualidade,
            registrar_avaliacao_offline=lambda: container.registrar_avaliacao_offline,
            avaliar_resumo=lambda: container.avaliar_resumo,
            versao_agente=container.versao_agente,
            whatsapp_twilio=lambda: container.webhook_twilio,
            whatsapp_meta=lambda: container.webhook_meta,
            protecao=protecao,
            exportar_dados_lead=lambda: container.exportar_dados_lead,
            anonimizar_lead=lambda: container.anonimizar_lead,
        ),
        routers=container.vertical.routers,
        ao_iniciar=[
            *([aquecer_embedding] if settings.embedding_carregar_no_inicio else []),
            semear_agenda,
            preparar_rastreio,
            recuperar_turnos,
        ],
        ao_encerrar=[container.encerrar],
    )


def texto_templates_whatsapp(settings: Settings | None = None) -> str:
    """docs/whatsapp-templates.md da vertical ativa (sem banco: só lê as declarações)."""
    settings = settings or obter_settings()
    pack = VERTICAIS[settings.vertical]()
    montada = pack.montar(
        InfraCompartilhada(
            sessoes=criar_fabrica_sessao(criar_engine(settings.database_url)),
            embedding=EmbeddingFastembed(
                settings.embedding_modelo, settings.embedding_dimensao, None
            ),
        )
    )
    usos: dict[str, list[str]] = {}
    for situacao, cadencia in montada.cadencias_followup.items():
        for numero, etapa in enumerate(cadencia.etapas, start=1):
            usos.setdefault(etapa.template.nome, []).append(
                f"follow-up {situacao.value}, etapa {numero}"
            )
    if montada.lembrete_agendamento:
        usos.setdefault(montada.lembrete_agendamento.template.nome, []).append(
            "lembrete de agendamento"
        )
    if montada.template_resposta_responsavel:
        usos.setdefault(montada.template_resposta_responsavel.nome, []).append(
            "resposta da equipe (tela Fila) fora da janela"
        )
    return documentar_templates(
        montada.templates,
        vertical=pack.nome,
        usos=usos,
        max_caracteres=settings.template_variavel_max_caracteres,
    )
