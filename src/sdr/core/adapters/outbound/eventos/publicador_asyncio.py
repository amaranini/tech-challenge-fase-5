"""PublicadorEventosPort in-process (asyncio) — adapter da POC.

`publicar` agenda uma tarefa e retorna na hora: os assinantes (ex.: GerarResumoHandoff)
rodam depois que a resposta do turno já saiu. Eventos do MESMO lead são entregues em
ordem, um lote por vez (trava por lead); leads diferentes rodam em paralelo. Erro de um
assinante é registrado e não derruba os demais.

Produção: outbox + fila (os eventos já estão em `lead_eventos`). Limitação aceita na POC:
lotes em andamento se perdem se o processo cair.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from uuid import UUID

from sdr.core.domain.eventos import EventoLead

logger = logging.getLogger(__name__)

Assinante = Callable[[Sequence[EventoLead]], Awaitable[object]]


class PublicadorEventosAsyncio:
    def __init__(self, assinantes: Sequence[Assinante] = ()) -> None:
        self._assinantes = list(assinantes)
        self._travas: dict[UUID, asyncio.Lock] = {}
        self._tarefas: set[asyncio.Task[None]] = set()

    def assinar(self, assinante: Assinante) -> None:
        self._assinantes.append(assinante)

    def publicar(self, eventos: Sequence[EventoLead]) -> None:
        por_lead: dict[UUID, list[EventoLead]] = {}
        for evento in eventos:
            por_lead.setdefault(evento.lead_id, []).append(evento)
        for lead_id, lote in por_lead.items():
            tarefa = asyncio.create_task(self._entregar(lead_id, tuple(lote)))
            self._tarefas.add(tarefa)
            tarefa.add_done_callback(self._tarefas.discard)

    async def _entregar(self, lead_id: UUID, lote: tuple[EventoLead, ...]) -> None:
        trava = self._travas.setdefault(lead_id, asyncio.Lock())
        async with trava:
            for assinante in self._assinantes:
                try:
                    await assinante(lote)
                except Exception:
                    logger.exception("Assinante falhou ao tratar eventos do lead %s", lead_id)

    async def aguardar_ociosidade(self) -> None:
        """Espera as entregas em andamento (testes/encerramento gracioso)."""
        while self._tarefas:
            await asyncio.gather(*list(self._tarefas), return_exceptions=True)

    async def encerrar(self, timeout_s: float = 30.0) -> None:
        try:
            await asyncio.wait_for(self.aguardar_ociosidade(), timeout_s)
        except TimeoutError:
            logger.warning(
                "Encerrando com %d entrega(s) de eventos pendente(s)", len(self._tarefas)
            )
