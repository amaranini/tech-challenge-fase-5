"""AgendadorTurnoPort in-process (asyncio) com debounce por lead — adapter da POC.

Cada `agendar(lead)` reinicia a janela de silêncio; o turno roda quando o lead fica
`janela_s` sem mandar nada OU quando o teto `teto_s` (contado da 1ª mensagem do ciclo)
estoura. Mensagens que chegam com o turno JÁ rodando abrem um novo ciclo: o ProcessarTurno
reprocessa se a resposta ainda não saiu, e a trava por lead serializa o resto.

Produção: trocar por fila/Redis (ex.: job com chave por lead + delay), mesma porta.
Limitação aceita na POC: o estado vive no processo (1 réplica da API); pendências de um
processo que morreu são recuperadas no start (`leads_com_pendentes`).
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Protocol
from uuid import UUID

logger = logging.getLogger(__name__)

Executor = Callable[[UUID], Awaitable[object]]


class Relogio(Protocol):
    def agora(self) -> float: ...

    async def dormir(self, segundos: float) -> None: ...


class RelogioAsyncio:
    def agora(self) -> float:
        return time.monotonic()

    async def dormir(self, segundos: float) -> None:
        await asyncio.sleep(segundos)


@dataclass
class _Ciclo:
    primeiro: float
    tarefa: "asyncio.Task[None] | None" = field(default=None)


class AgendadorDebounce:
    def __init__(
        self,
        janela_s: float,
        teto_s: float,
        executar: Executor | None = None,
        relogio: Relogio | None = None,
    ) -> None:
        if janela_s < 0 or teto_s < janela_s:
            raise ValueError("exige 0 <= janela_s <= teto_s")
        self._janela = janela_s
        self._teto = teto_s
        self._executar = executar
        self._relogio = relogio or RelogioAsyncio()
        self._aguardando: dict[UUID, _Ciclo] = {}
        self._tarefas: set[asyncio.Task[None]] = set()

    def definir_executor(self, executar: Executor) -> None:
        self._executar = executar

    def agendar(self, lead_id: UUID) -> None:
        agora = self._relogio.agora()
        ciclo = self._aguardando.get(lead_id)
        if ciclo is None:
            ciclo = self._aguardando[lead_id] = _Ciclo(primeiro=agora)
        elif ciclo.tarefa is not None:
            ciclo.tarefa.cancel()  # reinicia a janela de silêncio
        espera = min(self._janela, max(0.0, ciclo.primeiro + self._teto - agora))
        tarefa = asyncio.create_task(self._esperar_e_executar(lead_id, ciclo, espera))
        ciclo.tarefa = tarefa
        self._tarefas.add(tarefa)
        tarefa.add_done_callback(self._tarefas.discard)

    async def _esperar_e_executar(self, lead_id: UUID, ciclo: _Ciclo, espera: float) -> None:
        await self._relogio.dormir(espera)
        if self._aguardando.get(lead_id) is ciclo:
            del self._aguardando[lead_id]  # daqui em diante, nova mensagem abre outro ciclo
        if self._executar is None:
            logger.error("AgendadorDebounce sem executor; turno do lead %s ignorado", lead_id)
            return
        try:
            await self._executar(lead_id)
        except Exception:
            logger.exception("Erro ao processar turno do lead %s", lead_id)

    @property
    def aguardando(self) -> frozenset[UUID]:
        return frozenset(self._aguardando)

    async def aguardar_ociosidade(self) -> None:
        """Espera todas as tarefas terminarem (testes/encerramento gracioso)."""
        while self._tarefas:
            await asyncio.gather(*list(self._tarefas), return_exceptions=True)

    async def encerrar(self) -> None:
        for tarefa in list(self._tarefas):
            tarefa.cancel()
        await asyncio.gather(*list(self._tarefas), return_exceptions=True)
        self._aguardando.clear()
