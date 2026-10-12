"""Login do dashboard (ADR 015): sem credencial não entra; hash PBKDF2 confere."""

import hashlib
import os
import subprocess
import sys
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from web.autenticacao import senha_confere

APP = Path(__file__).resolve().parents[3] / "web" / "app.py"


def _hash(senha: str, sal: bytes = b"0123456789abcdef", iteracoes: int = 1000) -> str:
    calculado = hashlib.pbkdf2_hmac("sha256", senha.encode(), sal, iteracoes).hex()
    return f"pbkdf2_sha256:{iteracoes}:{sal.hex()}:{calculado}"


def test_senha_confere_com_hash_e_com_texto() -> None:
    assert senha_confere("s3nha", hash_salvo=_hash("s3nha"))
    assert not senha_confere("outra", hash_salvo=_hash("s3nha"))
    assert not senha_confere("s3nha", hash_salvo="lixo")
    assert senha_confere("dev", senha_plana="dev")
    assert not senha_confere("", senha_plana="")


@pytest.fixture
def app_com_login(monkeypatch: pytest.MonkeyPatch) -> AppTest:
    monkeypatch.setenv("API_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("DASHBOARD_USUARIO", "equipe")
    monkeypatch.setenv("DASHBOARD_SENHA_HASH", _hash("s3nha"))
    for modulo in ("web.comum", "web.autenticacao"):
        monkeypatch.delitem(sys.modules, modulo, raising=False)
    app = AppTest.from_file(str(APP), default_timeout=30)
    app.run()
    return app


def test_dashboard_pede_login_e_nao_mostra_dados(app_com_login: AppTest) -> None:
    app = app_com_login
    assert not app.exception
    assert app.title[0].value == "🔒 Entrar"
    assert len(app.text_input) == 2  # só usuário e senha: nenhuma página carregou


def test_senha_errada_nao_entra(app_com_login: AppTest) -> None:
    app = app_com_login
    app.text_input[0].input("equipe")
    app.text_input[1].input("errada")
    app.button[0].click().run()
    assert app.error[0].value == "Usuário ou senha inválidos."
    assert "usuario_logado" not in app.session_state


def test_senha_certa_entra(app_com_login: AppTest) -> None:
    app = app_com_login
    app.text_input[0].input("equipe")
    app.text_input[1].input("s3nha")
    app.button[0].click().run()
    assert app.session_state["usuario_logado"] == "equipe"
    assert not [t for t in app.title if t.value == "🔒 Entrar"]


def test_producao_sem_credencial_bloqueia(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AMBIENTE", "producao")
    for var in ("DASHBOARD_USUARIO", "DASHBOARD_SENHA_HASH", "DASHBOARD_SENHA"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.delitem(sys.modules, "web.autenticacao", raising=False)
    app = AppTest.from_file(str(APP), default_timeout=30)
    app.run()
    assert "sem credenciais" in app.error[0].value


def test_app_abre_fora_da_raiz_como_na_imagem(tmp_path: Path) -> None:
    """Na imagem, `streamlit run web/app.py` não põe a raiz no sys.path: o app.py
    precisa achar o pacote `web` sozinho (quebrou no Dia 5 — só o pytest tinha a raiz)."""
    script = (
        "from streamlit.testing.v1 import AppTest\n"
        f"a = AppTest.from_file({str(APP)!r}, default_timeout=30)\n"
        "a.run()\n"
        "assert not a.exception, [e.value for e in a.exception]\n"
    )
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    resultado = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert resultado.returncode == 0, resultado.stderr[-2000:]
