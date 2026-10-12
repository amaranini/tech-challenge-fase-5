"""Login do dashboard (ADR 015): usuários e papéis via env.

- DASHBOARD_USUARIO + DASHBOARD_SENHA_HASH ("pbkdf2_sha256:<iteracoes>:<sal>:<hash>",
  gerado por `python -m sdr.cli hash-senha`) — ou DASHBOARD_SENHA em texto (só local):
  a equipe, papel `admin`.
- DASHBOARD_USUARIOS (JSON) acrescenta usuários com papel, ex.: o de avaliação
  {"avaliador": {"senha_hash": "pbkdf2_sha256:…", "papel": "leitura"}}. Papel `leitura`:
  vê tudo, não age (sem assumir, responder, devolver, avaliar) e vê telefone, e-mail e CPF
  mascarados; fala com a API pela chave somente leitura (GET; nunca LGPD).
- Sem credencial configurada: aberto em ambiente local (com aviso); em produção, bloqueia.
"""

import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass

import streamlit as st

PRODUCAO = os.environ.get("AMBIENTE", "local") == "producao"
ESPERA_APOS_ERRO_S = 1.0  # desacelera tentativa de senha por força bruta
ADMIN, LEITURA = "admin", "leitura"


@dataclass(frozen=True)
class Usuario:
    nome: str
    senha_hash: str = ""
    senha_plana: str = ""
    papel: str = ADMIN


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


def usuarios() -> dict[str, Usuario]:
    """Usuários configurados (equipe + os de DASHBOARD_USUARIOS)."""
    todos: dict[str, Usuario] = {}
    nome = os.environ.get("DASHBOARD_USUARIO", "")
    senha_hash = os.environ.get("DASHBOARD_SENHA_HASH", "")
    senha_plana = os.environ.get("DASHBOARD_SENHA", "")
    if nome and (senha_hash or senha_plana):
        todos[nome] = Usuario(nome, senha_hash, senha_plana, ADMIN)
    try:
        extras = json.loads(os.environ.get("DASHBOARD_USUARIOS") or "{}")
    except json.JSONDecodeError:
        extras = {}
    for nome_extra, dados in extras.items() if isinstance(extras, dict) else []:
        if isinstance(dados, dict) and dados.get("senha_hash"):
            papel = LEITURA if dados.get("papel") == LEITURA else ADMIN
            todos[nome_extra] = Usuario(nome_extra, str(dados["senha_hash"]), "", papel)
    return todos


def somente_leitura() -> bool:
    return st.session_state.get("papel") == LEITURA


def exigir_login() -> str | None:
    """Mostra o login e interrompe a página até autenticar. Devolve o usuário logado."""
    cadastrados = usuarios()
    if not cadastrados:
        if PRODUCAO:
            st.error("Dashboard sem credenciais configuradas (DASHBOARD_USUARIO/SENHA_HASH).")
            st.stop()
        st.sidebar.warning("Login desligado (configure DASHBOARD_USUARIO e a senha).")
        return None
    logado = st.session_state.get("usuario_logado")
    if logado in cadastrados:
        return str(logado)

    st.title("🔒 Entrar")
    with st.form("login"):
        informado = st.text_input("Usuário")
        senha = st.text_input("Senha", type="password")
        entrar = st.form_submit_button("Entrar", type="primary")
    if entrar:
        usuario = next(
            (u for nome, u in cadastrados.items() if hmac.compare_digest(informado, nome)), None
        )
        if usuario and senha_confere(
            senha, hash_salvo=usuario.senha_hash, senha_plana=usuario.senha_plana
        ):
            st.session_state["usuario_logado"] = usuario.nome
            st.session_state["papel"] = usuario.papel
            st.rerun()
        time.sleep(ESPERA_APOS_ERRO_S)
        st.error("Usuário ou senha inválidos.")
    st.stop()
    return None  # inalcançável: st.stop interrompe o script


def botao_sair() -> None:
    if st.session_state.get("usuario_logado"):
        papel = " · somente leitura" if somente_leitura() else ""
        st.sidebar.caption(f"👤 {st.session_state['usuario_logado']}{papel}")
        if st.sidebar.button("Sair"):
            st.session_state.pop("usuario_logado", None)
            st.session_state.pop("papel", None)
            st.rerun()
