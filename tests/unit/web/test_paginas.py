"""Cada página do dashboard abre sem exceção mesmo com a API fora do ar (avisa e segue)."""

import sys
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

PAGINAS = Path(__file__).resolve().parents[3] / "web" / "paginas"


@pytest.mark.parametrize("pagina", ["visao_geral", "leads", "fila", "agenda", "followups", "chat"])
def test_pagina_abre_sem_api(pagina: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_URL", "http://127.0.0.1:9")  # porta fechada: conexão recusada
    monkeypatch.delitem(sys.modules, "web.comum", raising=False)  # relê o API_URL
    app = AppTest.from_file(str(PAGINAS / f"{pagina}.py"), default_timeout=30)
    app.run()
    assert not app.exception, [e.value for e in app.exception]
