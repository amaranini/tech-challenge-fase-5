"""Fila de follow-ups (retomadas, lembretes, SLA do handoff) e o redator das mensagens."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol
from uuid import UUID

from sdr.core.domain.agenda import Agendamento
from sdr.core.domain.catalogo import ItemCatalogo
from sdr.core.domain.conversa import Lead, Mensagem
from sdr.core.domain.followup import FollowUp, StatusFollowUp, TipoFollowUp


class FollowUpRepository(Protocol):
    async def agendar(self, followup: FollowUp) -> None: ...

    async def cancelar_pendentes(
        self, lead_id: UUID, tipos: Sequence[TipoFollowUp], motivo: str
    ) -> int:
        """Cancela os PENDENTES desses tipos; devolve quantos."""
        ...

    async def reservar_vencidos(
        self, agora: datetime, limite: int, *, travado_ha: float = 600
    ) -> list[FollowUp]:
        """Pega até `limite` vencidos (executar_em <= agora) e os marca PROCESSANDO numa
        transação com `FOR UPDATE SKIP LOCKED` — vários workers nunca pegam o mesmo. Itens
        PROCESSANDO há mais de `travado_ha` segundos (worker caiu) voltam a ser elegíveis."""
        ...

    async def concluir(
        self, followup_id: UUID, status: StatusFollowUp, motivo: str | None
    ) -> None: ...

    async def reagendar(self, followup: FollowUp) -> None:
        """Volta a PENDENTE com o novo `executar_em` (adiado, ex.: fora do horário)."""
        ...

    async def pendentes(self, lead_id: UUID) -> list[FollowUp]:
        """Em ordem de execução."""
        ...


@dataclass(frozen=True)
class PedidoMensagemAtiva:
    """Mensagem que o assistente manda SEM o lead ter falado (retomada ou lembrete)."""

    lead: Lead
    historico: Sequence[Mensagem]
    objetivo: str  # o que esta mensagem precisa fazer (da cadência/lembrete)
    encerramento: bool = False
    itens_novos: Sequence[ItemCatalogo] = field(default_factory=tuple)  # algo de valor
    agendamento: Agendamento | None = None
    instrucao_adicional: str | None = None  # ex.: correção (código inexistente)


@dataclass(frozen=True)
class MensagemAtiva:
    texto: str
    modelo: str | None = None
    tokens_entrada: int = 0
    tokens_saida: int = 0


class RedatorMensagemAtivaPort(Protocol):
    async def redigir(self, pedido: PedidoMensagemAtiva) -> MensagemAtiva: ...
