"""O core é genérico: nenhum arquivo dele pode falar de uma vertical específica.

Complementa o import-linter (que barra imports): aqui barramos também vocabulário
(nomes, docstrings, comentários) que indicaria acoplamento conceitual.
"""

import re
from pathlib import Path

import pytest

import sdr.core

RAIZ_CORE = Path(sdr.core.__file__).parent
VOCABULARIO_DE_VERTICAL = re.compile(r"im[oó]ve(l|is)|imobili|corretor|\bmetr[oô]\b", re.IGNORECASE)


@pytest.mark.parametrize(
    "arquivo",
    sorted([*RAIZ_CORE.rglob("*.py"), *RAIZ_CORE.rglob("*.md")]),
    ids=lambda p: str(p.relative_to(RAIZ_CORE)),
)
def test_core_nao_menciona_vertical(arquivo: Path) -> None:
    ocorrencias = [
        f"{n}: {linha.strip()}"
        for n, linha in enumerate(arquivo.read_text(encoding="utf-8").splitlines(), start=1)
        if VOCABULARIO_DE_VERTICAL.search(linha)
    ]
    assert not ocorrencias, f"{arquivo} menciona a vertical:\n" + "\n".join(ocorrencias)
