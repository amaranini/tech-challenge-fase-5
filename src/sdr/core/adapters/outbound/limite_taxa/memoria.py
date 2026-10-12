"""LimitadorTaxaPort em memória: token bucket por chave (ADR 015).

Vale para UM processo — coerente com a API de 1 réplica da POC. Produção com várias
réplicas: adapter Redis (INCR + EXPIRE, ou script de token bucket) no lugar deste.
"""

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass

MAX_CHAVES = 50_000  # teto de memória: acima disso, descarta os baldes cheios (ociosos)


@dataclass
class _Balde:
    fichas: float
    atualizado: float


class LimitadorTaxaMemoria:
    def __init__(self, relogio: Callable[[], float] = time.monotonic) -> None:
        self._relogio = relogio
        self._baldes: dict[str, _Balde] = {}
        self._trava = asyncio.Lock()

    async def consumir(self, chave: str, limite_por_minuto: int) -> float:
        if limite_por_minuto <= 0:
            return 0.0  # limite desligado
        taxa = limite_por_minuto / 60.0  # fichas por segundo
        async with self._trava:
            agora = self._relogio()
            balde = self._baldes.get(chave)
            if balde is None:
                if len(self._baldes) >= MAX_CHAVES:
                    self._podar(agora, taxa, limite_por_minuto)
                balde = self._baldes[chave] = _Balde(float(limite_por_minuto), agora)
            balde.fichas = min(
                float(limite_por_minuto), balde.fichas + (agora - balde.atualizado) * taxa
            )
            balde.atualizado = agora
            if balde.fichas >= 1:
                balde.fichas -= 1
                return 0.0
            return (1 - balde.fichas) / taxa

    def _podar(self, agora: float, taxa: float, capacidade: int) -> None:
        cheios = [
            chave
            for chave, b in self._baldes.items()
            if b.fichas + (agora - b.atualizado) * taxa >= capacidade
        ]
        for chave in cheios or list(self._baldes)[: len(self._baldes) // 2]:
            del self._baldes[chave]
