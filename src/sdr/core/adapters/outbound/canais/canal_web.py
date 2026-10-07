"""CanalMensagemPort do chat web: a resposta já está persistida e o front a busca por
polling (GET /conversas/{lead_id}/mensagens) — não há transporte a fazer."""

import logging
from collections.abc import Mapping

from sdr.core.domain.conversa import Lead, Mensagem

logger = logging.getLogger(__name__)


class CanalWeb:
    async def enviar(self, lead: Lead, mensagem: Mensagem) -> None:
        logger.debug("Resposta %s disponível para o lead web %s", mensagem.id, lead.remetente_id)

    async def enviar_template(
        self, lead: Lead, template: str, variaveis: Mapping[str, str]
    ) -> None:
        # Na web não há janela de 24h nem templates; follow-up web fica para o Dia 3.
        logger.warning("Template %r ignorado no canal web (lead %s)", template, lead.remetente_id)
