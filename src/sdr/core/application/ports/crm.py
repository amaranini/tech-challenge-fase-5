"""CRM: onde o time comercial acompanha o lead.

POC: mock em Postgres (tabela crm_registros) + log JSON. Produção: HubSpot (ou outro CRM)
— contato/negócio criado ou atualizado pela API, resumo anexado como nota. Troca-se só o
adapter.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from sdr.core.domain.agenda import Agendamento
from sdr.core.domain.conversa import Lead
from sdr.core.domain.resumo import Resumo


@dataclass(frozen=True)
class RegistroCRM:
    crm_id: str  # id do lead no CRM
    criado: bool  # False = atualizado
    atualizado_em: datetime


class CRMPort(Protocol):
    async def registrar(
        self, lead: Lead, resumo: Resumo, agendamento: Agendamento | None
    ) -> RegistroCRM:
        """Cria ou atualiza o lead no CRM e anexa o resumo (idempotente por lead+versão)."""
        ...
