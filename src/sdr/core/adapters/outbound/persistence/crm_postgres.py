"""CRMPort mock: tabela crm_registros (um registro por lead, upsert) + log JSON Lines.

Produção: HubSpot (ou outro CRM). `registrar` vira "upsert de contato/negócio pelo id
externo + nota com o resumo" na API do CRM; troca-se só este adapter. O log JSON simula o
payload que seria enviado (útil para conferir na demo: `tail -f var/crm_mock.jsonl`).
"""

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sdr.core.adapters.outbound.persistence.modelos import CrmRegistroModel
from sdr.core.adapters.outbound.persistence.resumos_sql import secoes_para_json
from sdr.core.application.ports.crm import RegistroCRM
from sdr.core.domain.agenda import Agendamento
from sdr.core.domain.conversa import Lead, agora
from sdr.core.domain.resumo import Resumo

logger = logging.getLogger(__name__)


def payload_crm(lead: Lead, resumo: Resumo, agendamento: Agendamento | None) -> dict[str, Any]:
    q = lead.qualificacao
    return {
        "lead": {
            "id": str(lead.id),
            "canal": lead.canal.value,
            "remetente_id": lead.remetente_id,
            "nome": lead.nome,
            "intencao": q.intencao_atual,
            "score": q.score.pontos if q.score else None,
            "classificacao": q.score.classificacao.value if q.score else None,
            "proxima_acao": q.proxima_acao,
        },
        "agendamento": (
            {
                "id": str(agendamento.id),
                "tipo": agendamento.tipo,
                "inicio": agendamento.inicio.isoformat(),
                "modalidade": agendamento.modalidade,
                "status": agendamento.status.value,
                "responsavel": agendamento.responsavel.nome,
            }
            if agendamento
            else None
        ),
    }


class CRMPostgresMock:
    def __init__(self, sessoes: async_sessionmaker[AsyncSession], log: Path | None) -> None:
        self._sessoes = sessoes
        self._log = log

    async def registrar(
        self, lead: Lead, resumo: Resumo, agendamento: Agendamento | None
    ) -> RegistroCRM:
        momento = agora()
        dados = payload_crm(lead, resumo, agendamento)
        anexo = {
            "versao": resumo.versao,
            "titulo": resumo.titulo,
            "gerado_em": resumo.gerado_em.isoformat(),
            "secoes": secoes_para_json(resumo),
        }
        async with self._sessoes.begin() as sessao:
            existente = await sessao.scalar(
                select(CrmRegistroModel.crm_id).where(CrmRegistroModel.lead_id == lead.id)
            )
            crm_id = existente or f"CRM-{lead.id.hex[:8].upper()}"
            stmt = insert(CrmRegistroModel).values(
                lead_id=lead.id,
                crm_id=crm_id,
                dados=dados,
                resumo_versao=resumo.versao,
                resumo=anexo,
                criado_em=momento,
                atualizado_em=momento,
            )
            await sessao.execute(
                stmt.on_conflict_do_update(
                    index_elements=[CrmRegistroModel.lead_id],
                    set_={
                        "dados": stmt.excluded.dados,
                        "resumo_versao": stmt.excluded.resumo_versao,
                        "resumo": stmt.excluded.resumo,
                        "atualizado_em": stmt.excluded.atualizado_em,
                    },
                )
            )
        registro = RegistroCRM(crm_id, criado=existente is None, atualizado_em=momento)
        await self._registrar_log(
            {
                "em": momento.isoformat(),
                "operacao": "criar" if registro.criado else "atualizar",
                "crm_id": crm_id,
                **dados,
                "resumo": anexo,
            }
        )
        return registro

    async def _registrar_log(self, linha: dict[str, Any]) -> None:
        texto = json.dumps(linha, ensure_ascii=False, default=str)
        logger.info("CRM mock: %s %s", linha["operacao"], linha["crm_id"])
        if self._log is None:
            return
        caminho = self._log

        def escrever() -> None:
            caminho.parent.mkdir(parents=True, exist_ok=True)
            with caminho.open("a", encoding="utf-8") as arquivo:
                arquivo.write(texto + "\n")

        await asyncio.to_thread(escrever)
