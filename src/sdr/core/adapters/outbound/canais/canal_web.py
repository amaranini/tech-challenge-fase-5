"""CanalMensagemPort do chat web: a resposta já está persistida e o front a busca por
polling (GET /conversas/{lead_id}/mensagens) — não há transporte a fazer.

Templates (fora da janela de conversa) não existem na web: o core já gravou o texto do
template renderizado na mensagem, que o chat mostra como PREVIEW de como sairia no
WhatsApp (útil na demo).
"""

import logging
from collections.abc import Mapping

from sdr.core.application.ports.canal import ResultadoEnvio
from sdr.core.domain.conversa import Lead, Mensagem

logger = logging.getLogger(__name__)


class CanalWeb:
    async def enviar_texto(self, lead: Lead, mensagem: Mensagem) -> ResultadoEnvio:
        logger.debug("Resposta %s disponível para o lead web %s", mensagem.id, lead.id)
        return ResultadoEnvio()

    async def enviar_template(
        self,
        lead: Lead,
        mensagem: Mensagem,
        nome_logico: str,
        variaveis: Mapping[str, str],
    ) -> ResultadoEnvio:
        logger.info(
            "Preview do template %r para o lead web %s (%d variável(is))",
            nome_logico,
            lead.id,
            len(variaveis),
        )
        return ResultadoEnvio()
