"""Entrega de mensagens de saída — a resposta da IA, a do responsável, o follow-up e o
lembrete passam todos por aqui, pelo canal de origem do lead.

A decisão é do core: dentro da janela de conversa (contada da última mensagem do LEAD),
texto livre; fora dela, template aprovado — preenchido a partir do contexto, com todas as
variáveis validadas e fallback para o valor padrão. Sem template (ou com template não
mapeado no provedor) NADA sai como texto livre: a mensagem fica registrada como não
enviada e o evento EnvioTemplateIndisponivel avisa a operação.

Em duas fases, para quem chama controlar a persistência: `preparar` monta a mensagem já
decidida (texto livre, template renderizado ou bloqueada); `transmitir` executa no canal e
registra o resultado de entrega.
"""

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sdr.core.application.ports.canal import (
    CanalMensagemPort,
    FalhaEnvioError,
    TemplateIndisponivelError,
)
from sdr.core.application.ports.relogio import RelogioPort
from sdr.core.application.ports.repositorios import (
    ConversaRepository,
    EntregaRepository,
    LeadEventoRepository,
)
from sdr.core.domain.conversa import (
    Canal,
    Conversa,
    Lead,
    Mensagem,
    Papel,
    StatusEntrega,
    StatusMensagem,
)
from sdr.core.domain.eventos import EventoLead, TipoEvento
from sdr.core.domain.followup import requer_template
from sdr.core.domain.template import (
    MAX_CARACTERES_VARIAVEL,
    ContextoTemplate,
    TemplateLogico,
    preencher_template,
)

logger = logging.getLogger(__name__)

TEXTO = "texto"
TEMPLATE = "template"
BLOQUEADO = "bloqueado"


@dataclass(frozen=True)
class ConfigEntrega:
    fuso: ZoneInfo = field(default_factory=lambda: ZoneInfo("America/Sao_Paulo"))
    janela_conversa: timedelta = timedelta(hours=24)
    max_caracteres_variavel: int = MAX_CARACTERES_VARIAVEL


class EntregarMensagem:
    def __init__(
        self,
        conversas: ConversaRepository,
        entregas: EntregaRepository,
        eventos: LeadEventoRepository,
        canais: Mapping[Canal, CanalMensagemPort],
        *,
        relogio: RelogioPort,
        config: ConfigEntrega | None = None,
    ) -> None:
        self._conversas = conversas
        self._entregas = entregas
        self._eventos = eventos
        self._canais = canais
        self._relogio = relogio
        self.config = config or ConfigEntrega()

    # ------------------------------------------------------------------ decisão
    async def fora_da_janela(self, conversa: Conversa) -> bool:
        ultima = await self._conversas.ultima_do_lead(conversa.id)
        return requer_template(ultima, self._relogio.agora(), self.config.janela_conversa)

    def contexto(self, lead: Lead, **extra: object) -> ContextoTemplate:
        return ContextoTemplate(lead, self.config.fuso, **extra)  # type: ignore[arg-type]

    def em_texto(
        self,
        conversa: Conversa,
        papel: Papel,
        texto: str,
        metadados: Mapping[str, object],
        *,
        criada_em: datetime | None = None,
    ) -> Mensagem:
        return Mensagem.nova(
            conversa.id,
            papel,
            texto,
            metadados={**metadados, "envio": {"modo": TEXTO}},
            criada_em=criada_em or self._relogio.agora(),
        )

    def em_template(
        self,
        conversa: Conversa,
        papel: Papel,
        template: TemplateLogico | None,
        contexto: ContextoTemplate,
        metadados: Mapping[str, object],
        *,
        texto_original: str = "",
        criada_em: datetime | None = None,
    ) -> Mensagem:
        """Fora da janela. `texto_original`: o que se diria em texto livre (fica registrado
        para a equipe; não é enviado)."""
        momento = criada_em or self._relogio.agora()
        if template is None:
            return Mensagem.nova(
                conversa.id,
                papel,
                texto_original or "(mensagem ativa sem template)",
                metadados={
                    **metadados,
                    "envio": {"modo": BLOQUEADO, "motivo": "sem_template_logico"},
                },
                criada_em=momento,
                status=StatusMensagem.NAO_ENVIADA,
            )
        preenchido = preencher_template(template, contexto, self.config.max_caracteres_variavel)
        if preenchido.fallbacks:
            logger.info(
                "Template %s: variável(is) %s no valor padrão", template.nome, preenchido.fallbacks
            )
        return Mensagem.nova(
            conversa.id,
            papel,
            preenchido.texto,
            metadados={
                **metadados,
                "envio": {
                    "modo": TEMPLATE,
                    "template": template.nome,
                    "variaveis": preenchido.valores,
                    "fallbacks": list(preenchido.fallbacks),
                    **({"texto_original": texto_original} if texto_original else {}),
                },
            },
            criada_em=momento,
        )

    async def preparar(
        self,
        lead: Lead,
        conversa: Conversa,
        papel: Papel,
        texto: str,
        metadados: Mapping[str, object],
        *,
        template: TemplateLogico | None = None,
        contexto: ContextoTemplate | None = None,
        criada_em: datetime | None = None,
    ) -> Mensagem:
        if not await self.fora_da_janela(conversa):
            return self.em_texto(conversa, papel, texto, metadados, criada_em=criada_em)
        return self.em_template(
            conversa,
            papel,
            template,
            contexto or self.contexto(lead),
            metadados,
            texto_original=texto,
            criada_em=criada_em,
        )

    # ------------------------------------------------------------------ execução
    async def transmitir(self, lead: Lead, mensagem: Mensagem) -> StatusEntrega:
        """Executa no canal do lead a decisão já gravada em `mensagem.metadados["envio"]`
        (a mensagem já deve estar persistida)."""
        envio = mensagem.metadados.get("envio")
        envio = envio if isinstance(envio, Mapping) else {"modo": TEXTO}
        agora = self._relogio.agora()
        modo = envio.get("modo")
        if modo == BLOQUEADO:
            return await self._falhar(
                lead,
                mensagem,
                TipoEvento.ENVIO_TEMPLATE_INDISPONIVEL,
                str(envio.get("motivo")),
                nao_enviada=True,
                template=None,
            )
        canal = self._canais.get(lead.canal)
        if canal is None:
            return await self._falhar(
                lead,
                mensagem,
                TipoEvento.MENSAGEM_NAO_ENTREGUE,
                f"sem adapter para o canal {lead.canal.value}",
                nao_enviada=True,
            )
        try:
            if modo == TEMPLATE:
                variaveis = envio.get("variaveis")
                resultado = await canal.enviar_template(
                    lead,
                    mensagem,
                    str(envio.get("template")),
                    dict(variaveis) if isinstance(variaveis, Mapping) else {},
                )
            else:
                resultado = await canal.enviar_texto(lead, mensagem)
        except TemplateIndisponivelError as erro:
            return await self._falhar(
                lead,
                mensagem,
                TipoEvento.ENVIO_TEMPLATE_INDISPONIVEL,
                str(erro),
                nao_enviada=True,
                template=envio.get("template"),
            )
        except FalhaEnvioError as erro:
            return await self._falhar(
                lead, mensagem, TipoEvento.MENSAGEM_NAO_ENTREGUE, str(erro), nao_enviada=False
            )
        await self._entregas.registrar_envio(mensagem.id, resultado.ids_externos, agora)
        return StatusEntrega.ENVIADA

    async def _falhar(
        self,
        lead: Lead,
        mensagem: Mensagem,
        tipo: TipoEvento,
        motivo: str,
        *,
        nao_enviada: bool,
        **extra: object,
    ) -> StatusEntrega:
        agora = self._relogio.agora()
        logger.warning("Mensagem %s não enviada ao lead %s: %s", mensagem.id, lead.id, motivo)
        await self._entregas.marcar_falha(mensagem.id, motivo, agora, nao_enviada=nao_enviada)
        await self._eventos.registrar(
            [
                EventoLead(
                    lead.id,
                    tipo,
                    agora,
                    {
                        "mensagem_id": str(mensagem.id),
                        "canal": lead.canal.value,
                        "motivo": motivo,
                        **extra,
                    },
                )
            ]
        )
        return StatusEntrega.FALHOU


class AtualizarStatusEntrega:
    """Callback de status do provedor (enviada/entregue/lida/falhou) → mensagem."""

    def __init__(
        self, entregas: EntregaRepository, eventos: LeadEventoRepository, relogio: RelogioPort
    ) -> None:
        self._entregas = entregas
        self._eventos = eventos
        self._relogio = relogio

    async def executar(
        self, id_externo: str, status: StatusEntrega, erro: str | None = None
    ) -> bool:
        """False se o id é desconhecido (ex.: callback antes de o envio ser registrado)."""
        agora = self._relogio.agora()
        atualizacao = await self._entregas.atualizar(id_externo, status, erro, agora)
        if atualizacao is None:
            logger.info("Status %s para id externo desconhecido %s", status, id_externo)
            return False
        if atualizacao.atual is StatusEntrega.FALHOU and atualizacao.anterior is not (
            StatusEntrega.FALHOU
        ):
            await self._eventos.registrar(
                [
                    EventoLead(
                        atualizacao.lead_id,
                        TipoEvento.MENSAGEM_NAO_ENTREGUE,
                        agora,
                        {
                            "mensagem_id": str(atualizacao.mensagem_id),
                            "id_externo": id_externo,
                            "motivo": erro or "falha informada pelo provedor",
                        },
                    )
                ]
            )
        return True
