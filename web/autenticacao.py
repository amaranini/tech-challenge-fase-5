"""Login do dashboard (ADR 015): usuário e senha da operação via env.

- DASHBOARD_USUARIO + DASHBOARD_SENHA_HASH ("pbkdf2_sha256:<iteracoes>:<sal>:<hash>",
  gerado por `python -m sdr.cli hash-senha`) — ou DASHBOARD_SENHA em texto (só local).
- Sem credencial configurada: aberto em ambiente local (com aviso); em produção, bloqueia.
- O front fala com a API com a chave de admin (API_KEY), nunca com a do usuário.
"""

import hashlib
import hmac
import os
import time

import streamlit as st

PRODUCAO = os.environ.get("AMBIENTE", "local") == "producao"
ESPERA_APOS_ERRO_S = 1.0  # desacelera tentativa de senha por força bruta


def senha_confere(senha: str, *, hash_salvo: str = "", senha_plana: str = "") -> bool:
    if hash_salvo:
        try:
            algoritmo, iteracoes, sal, esperado = hash_salvo.split(":")
        except ValueError:
            return False
        if algoritmo != "pbkdf2_sha256":
            return False
        calculado = hashlib.pbkdf2_hmac(
            "sha256", senha.encode(), bytes.fromhex(sal), int(iteracoes)
        ).hex()
        return hmac.compare_digest(calculado, esperado)
    return bool(senha_plana) and hmac.compare_digest(senha.encode(), senha_plana.encode())


def _credenciais() -> tuple[str, str, str]:
    return (
        os.environ.get("DASHBOARD_USUARIO", ""),
        os.environ.get("DASHBOARD_SENHA_HASH", ""),
        os.environ.get("DASHBOARD_SENHA", ""),
    )


def exigir_login() -> str | None:
    """Mostra o login e interrompe a página até autenticar. Devolve o usuário logado."""
    usuario, hash_salvo, senha_plana = _credenciais()
    if not usuario or not (hash_salvo or senha_plana):
        if PRODUCAO:
            st.error("Dashboard sem credenciais configuradas (DASHBOARD_USUARIO/SENHA_HASH).")
            st.stop()
        st.sidebar.warning("Login desligado (configure DASHBOARD_USUARIO e a senha).")
        return None
    if st.session_state.get("usuario_logado") == usuario:
        return usuario

    st.title("🔒 Entrar")
    with st.form("login"):
        informado = st.text_input("Usuário")
        senha = st.text_input("Senha", type="password")
        entrar = st.form_submit_button("Entrar", type="primary")
    if entrar:
        usuario_ok = hmac.compare_digest(informado.encode(), usuario.encode())
        if usuario_ok and senha_confere(senha, hash_salvo=hash_salvo, senha_plana=senha_plana):
            st.session_state["usuario_logado"] = usuario
            st.rerun()
        time.sleep(ESPERA_APOS_ERRO_S)
        st.error("Usuário ou senha inválidos.")
    st.stop()
    return None  # inalcançável: st.stop interrompe o script


def botao_sair() -> None:
    if st.session_state.get("usuario_logado"):
        st.sidebar.caption(f"👤 {st.session_state['usuario_logado']}")
        if st.sidebar.button("Sair"):
            st.session_state.pop("usuario_logado", None)
            st.rerun()
