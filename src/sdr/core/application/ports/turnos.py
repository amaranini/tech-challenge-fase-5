"""Processamento assíncrono de turnos: agendamento com debounce e trava por lead."""

from contextlib import AbstractAsyncContextManager
from typing import Protocol
from uuid import UUID


class AgendadorTurnoPort(Protocol):
    """Agenda o processamento do turno de um lead para quando ele "parar de digitar".

    Cada chamada para o mesmo lead reinicia a janela de silêncio (debounce), respeitando um
    teto máximo de espera desde a primeira mensagem do turno. Retorna imediatamente.
    POC: asyncio in-process. Produção: fila/Redis — troca-se só o adapter.
    """

    def agendar(self, lead_id: UUID) -> None: ...


class TravaTurnoPort(Protocol):
    """Exclusão mútua por lead: nunca dois turnos do mesmo lead em paralelo."""

    def travar(self, lead_id: UUID) -> AbstractAsyncContextManager[bool]:
        """`async with trava.travar(id) as obtida:` — obtida=False se outro processo a detém."""
        ...
