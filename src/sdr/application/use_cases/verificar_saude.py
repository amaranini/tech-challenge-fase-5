import asyncio
from collections.abc import Sequence
from dataclasses import dataclass

from sdr.application.ports.saude import ResultadoVerificacao, VerificadorSaudePort


@dataclass(frozen=True)
class StatusSaude:
    componentes: tuple[ResultadoVerificacao, ...]

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.componentes)


class VerificarSaude:
    def __init__(self, verificadores: Sequence[VerificadorSaudePort]) -> None:
        self._verificadores = verificadores

    async def executar(self) -> StatusSaude:
        resultados = await asyncio.gather(*(v.verificar() for v in self._verificadores))
        return StatusSaude(componentes=tuple(resultados))
