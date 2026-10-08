from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from sdr.core.domain.atendimento import Atendimento, EstadoAtendimento
from sdr.core.domain.conversa import Canal, Conversa, Lead, Mensagem, StatusMensagem
from sdr.core.domain.eventos import EventoLead


@dataclass(frozen=True)
class ResumoLead:
    lead: Lead
    total_mensagens: int
    ultima_interacao_em: datetime | None


class LeadRepository(Protocol):
    async def obter(self, lead_id: UUID) -> Lead | None: ...

    async def obter_por_remetente(self, canal: Canal, remetente_id: str) -> Lead | None: ...

    async def salvar(self, lead: Lead) -> None:
        """Insere ou atualiza (por id)."""
        ...

    async def listar(self, canal: Canal | None, limite: int) -> list[ResumoLead]:
        """Mais recentes primeiro (por última interação)."""
        ...

    async def salvar_atendimento(
        self, atendimento: Atendimento, esperado: EstadoAtendimento
    ) -> bool:
        """Grava o estado de atendimento SÓ se o atual ainda for `esperado` (compare-and-set:
        um turno da IA nunca sobrescreve o humano que assumiu no meio). `salvar` não mexe
        no atendimento."""
        ...

    async def registrar_opt_out(self, lead_id: UUID, momento: datetime) -> None:
        """Lead pediu para não receber mais mensagens ativas (`salvar` não mexe nisso)."""
        ...

    async def listar_por_atendimento(
        self, estados: Sequence[EstadoAtendimento], limite: int = 100
    ) -> list[Lead]:
        """Na ordem de chegada na fila (mais antigo primeiro)."""
        ...


class ConversaRepository(Protocol):
    async def obter_aberta(self, lead_id: UUID) -> Conversa | None: ...

    async def salvar(self, conversa: Conversa) -> None: ...

    async def adicionar_mensagem(self, mensagem: Mensagem) -> None: ...

    async def ultimas_mensagens(
        self, conversa_id: UUID, limite: int, *, incluir_pendentes: bool = False
    ) -> list[Mensagem]:
        """As `limite` mais recentes, em ordem cronológica (sem pendentes, por padrão)."""
        ...

    async def pendentes(self, conversa_id: UUID) -> list[Mensagem]:
        """Mensagens do lead ainda não respondidas, em ordem cronológica."""
        ...

    async def marcar_status(self, ids: Sequence[UUID], status: StatusMensagem) -> None: ...

    async def leads_com_pendentes(self) -> list[UUID]:
        """Para recuperar turnos interrompidos (ex.: reinício do processo)."""
        ...


class LeadEventoRepository(Protocol):
    async def registrar(self, eventos: Sequence[EventoLead]) -> None: ...

    async def listar(self, lead_id: UUID, limite: int = 200) -> list[EventoLead]:
        """Em ordem cronológica."""
        ...
