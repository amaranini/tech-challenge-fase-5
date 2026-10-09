from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from sdr.core.domain.atendimento import Atendimento, EstadoAtendimento
from sdr.core.domain.conversa import (
    Canal,
    Conversa,
    Lead,
    Mensagem,
    StatusEntrega,
    StatusMensagem,
)
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

    async def adicionar_recebida(self, mensagem: Mensagem) -> bool:
        """Grava a mensagem do lead; False se o `id_externo` já existe (o provedor
        reenviou o webhook) — nesse caso nada é gravado."""
        ...

    async def mensagem_externa_existe(self, id_externo: str) -> bool: ...

    async def ultima_do_lead(self, conversa_id: UUID) -> datetime | None:
        """Quando o lead falou pela última vez (inclui pendentes): base da janela."""
        ...

    async def ultimas_mensagens(
        self, conversa_id: UUID, limite: int, *, incluir_pendentes: bool = False
    ) -> list[Mensagem]:
        """As `limite` mais recentes, em ordem cronológica. Por padrão (memória do agente),
        sem pendentes e sem as que não chegaram a sair (NAO_ENVIADA); `incluir_pendentes`
        traz tudo (exibição)."""
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


@dataclass(frozen=True)
class AtualizacaoEntrega:
    lead_id: UUID
    mensagem_id: UUID
    anterior: StatusEntrega | None  # da mensagem (agregado das partes)
    atual: StatusEntrega | None


class EntregaRepository(Protocol):
    """Status de entrega das mensagens de saída, por parte enviada ao provedor."""

    async def registrar_envio(
        self, mensagem_id: UUID, ids_externos: Sequence[str], momento: datetime
    ) -> None:
        """Saiu para o provedor: cada parte (id externo) começa como ENVIADA."""
        ...

    async def marcar_falha(
        self, mensagem_id: UUID, erro: str, momento: datetime, *, nao_enviada: bool
    ) -> None:
        """Falhou antes de sair (`nao_enviada`: some da memória do agente)."""
        ...

    async def atualizar(
        self, id_externo: str, status: StatusEntrega, erro: str | None, momento: datetime
    ) -> AtualizacaoEntrega | None:
        """Callback do provedor (sem regredir o status); None se o id é desconhecido."""
        ...
