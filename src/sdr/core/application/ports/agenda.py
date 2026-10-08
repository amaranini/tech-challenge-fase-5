"""Agenda de responsáveis: disponibilidade e reservas.

POC: mock em Postgres (tabelas responsaveis, slots_agenda e agendamentos). Produção: Google
Calendar / Outlook (free/busy + criação de eventos) — troca-se só o adapter; o registro
local do agendamento continua sendo do sistema.
"""

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol
from uuid import UUID

from sdr.core.domain.agenda import (
    Agendamento,
    PedidoReserva,
    Responsavel,
    Slot,
    StatusAgendamento,
)


class AgendaPort(Protocol):
    async def listar_responsaveis(self) -> list[Responsavel]:
        """Responsáveis ativos."""
        ...

    async def listar_disponibilidade(
        self, responsavel_ids: Sequence[UUID], inicio: datetime, fim: datetime
    ) -> list[Slot]:
        """Slots LIVRES desses responsáveis que começam em [inicio, fim), em ordem."""
        ...

    async def reservar(self, pedido: PedidoReserva) -> Agendamento:
        """Idempotente por `pedido.chave_idempotencia` (repetir devolve o mesmo agendamento).
        Levanta `SlotIndisponivelError` se o slot já foi tomado por outro pedido."""
        ...

    async def remarcar(
        self, agendamento_id: UUID, novo_slot_id: UUID, modalidade: str
    ) -> Agendamento:
        """Move para outro slot e libera o anterior. Idempotente: se já estiver no novo slot,
        devolve como está. Levanta `SlotIndisponivelError` se o novo slot foi tomado."""
        ...

    async def cancelar(self, agendamento_id: UUID) -> Agendamento:
        """Cancela e libera o slot. Idempotente."""
        ...

    async def agendamento_ativo(self, lead_id: UUID, a_partir_de: datetime) -> Agendamento | None:
        """Agendamento ativo do lead que ainda não terminou (o próximo, se houver vários)."""
        ...

    async def listar_agendamentos(
        self,
        *,
        lead_id: UUID | None = None,
        a_partir_de: datetime | None = None,
        status: StatusAgendamento | None = None,
        limite: int = 100,
    ) -> list[Agendamento]:
        """Em ordem de início (o mais cedo primeiro)."""
        ...
