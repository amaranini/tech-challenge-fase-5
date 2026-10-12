"""O runner anonimiza o lead ao fim de cada cenário (libera a agenda mock), mesmo com erro."""

from pathlib import Path
from typing import Any

from evals.cenarios import Cenario, Fala
from evals.runner import Runner


class ApiQuebrada:
    def __init__(self) -> None:
        self.anonimizados: list[str] = []

    def falar(self, lead_id: str, *textos: str) -> Any:
        raise RuntimeError("API fora")

    def anonimizar(self, lead_id: str) -> bool:
        self.anonimizados.append(lead_id)
        return True


CENARIO = Cenario("x", "teste", [Fala("oi")], {}, Path("x.json"))


def test_limpa_mesmo_quando_o_roteiro_quebra() -> None:
    api = ApiQuebrada()
    resultado = Runner(api, juiz=None, producao=False).executar(CENARIO)  # type: ignore[arg-type]
    assert resultado.erros == ["execução: RuntimeError: API fora"]
    assert api.anonimizados == [resultado.lead_id]


def test_manter_nao_limpa() -> None:
    api = ApiQuebrada()
    Runner(api, juiz=None, producao=False, limpar=False).executar(CENARIO)  # type: ignore[arg-type]
    assert api.anonimizados == []
