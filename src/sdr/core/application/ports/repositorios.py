from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from sdr.core.domain.conversa import Canal, Conversa, Lead, Mensagem


@dataclass(frozen=True)
class ResumoLead:
    lead: Lead
    total_mensagens: int
    ultima_interacao_em: datetime | None


class LeadRepository(Protocol):
    async def obter_por_remetente(self, canal: Canal, remetente_id: str) -> Lead | None: ...

    async def salvar(self, lead: Lead) -> None:
        """Insere ou atualiza (por id)."""
        ...

    async def listar(self, canal: Canal | None, limite: int) -> list[ResumoLead]:
        """Mais recentes primeiro (por última interação)."""
        ...


class ConversaRepository(Protocol):
    async def obter_aberta(self, lead_id: UUID) -> Conversa | None: ...

    async def salvar(self, conversa: Conversa) -> None: ...

    async def adicionar_mensagem(self, mensagem: Mensagem) -> None: ...

    async def ultimas_mensagens(self, conversa_id: UUID, limite: int) -> list[Mensagem]:
        """As `limite` mais recentes, em ordem cronológica."""
        ...
