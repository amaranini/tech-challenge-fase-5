from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ResultadoVerificacao:
    componente: str
    ok: bool
    detalhe: str | None = None


class VerificadorSaudePort(Protocol):
    """Verifica um componente de infraestrutura (banco, LLM, fila...).

    Implementações não devem lançar exceção: falhas viram `ok=False`.
    """

    async def verificar(self) -> ResultadoVerificacao: ...
