"""Front Streamlit: chat com a Lia. Consome SOMENTE a API HTTP — nunca o banco."""

import os
import uuid
from typing import Any

import httpx
import streamlit as st

API_URL = os.environ.get("API_URL", "http://localhost:8000")
TIMEOUT = httpx.Timeout(10.0, connect=5.0)
POLLING_SEGUNDOS = 1.5


def api_get(caminho: str, **params: Any) -> Any:
    resposta = httpx.get(f"{API_URL}{caminho}", params=params, timeout=TIMEOUT)
    resposta.raise_for_status()
    return resposta.json()


def api_enviar(lead_id: str, texto: str) -> dict[str, Any]:
    resposta = httpx.post(
        f"{API_URL}/conversas/mensagens",
        json={"lead_id": lead_id, "texto": texto},
        timeout=TIMEOUT,
    )
    if resposta.is_error:
        detalhe = resposta.json().get("detail", resposta.text)
        raise RuntimeError(f"{resposta.status_code}: {detalhe}")
    corpo: dict[str, Any] = resposta.json()
    return corpo


def novo_lead_id() -> str:
    return f"lead-{uuid.uuid4().hex[:6]}"


def mostrar_itens(itens: list[dict[str, Any]]) -> None:
    if not itens:
        return
    with st.expander(f"🏠 {len(itens)} imóvel(is) citado(s) — dados da base"):
        for item in itens:
            st.markdown(f"**{item['id']}** · {item.get('titulo', '')}")
            st.caption(item.get("resumo", ""))


st.set_page_config(page_title="Lia — SDR Imobiliário", page_icon="🏠")

# ------------------------------------------------------------------ barra lateral: leads
with st.sidebar:
    st.header("Leads")
    try:
        leads: list[dict[str, Any]] = api_get("/leads")
    except httpx.HTTPError as erro:
        st.error(f"API indisponível em {API_URL}: {erro}")
        st.stop()

    if st.button("➕ Novo lead", use_container_width=True):
        st.session_state.lead_id = novo_lead_id()

    with st.form("abrir_lead", clear_on_submit=True):
        digitado = st.text_input("Abrir/criar pelo lead_id", placeholder="ex.: lead-ana")
        if st.form_submit_button("Abrir") and digitado.strip():
            st.session_state.lead_id = digitado.strip()

    ids = [lead["lead_id"] for lead in leads]
    if "lead_id" not in st.session_state:
        st.session_state.lead_id = ids[0] if ids else novo_lead_id()
    atual: str = st.session_state.lead_id
    opcoes = ids if atual in ids else [atual, *ids]
    rotulos = {
        lead["lead_id"]: f"{lead['lead_id']} ({lead['total_mensagens']} msgs)" for lead in leads
    }
    escolhido = st.radio(
        "Conversas",
        opcoes,
        index=opcoes.index(atual),
        format_func=lambda i: rotulos.get(i, f"{i} (novo)"),
    )
    if escolhido != atual:
        st.session_state.lead_id = escolhido
        st.rerun()
    st.caption(f"API: {API_URL}")

# ------------------------------------------------------------------ conversa
lead_id: str = st.session_state.lead_id
st.title("🏠 Lia — consultora da imobiliária")
st.caption(
    f"Conversando como **{lead_id}** · pode mandar várias mensagens seguidas: a Lia espera "
    "você terminar e responde ao conjunto · reabra este lead_id para continuar"
)

# O envio só registra a mensagem (HTTP 202); a resposta chega pelo polling abaixo.
if texto := st.chat_input("Escreva como se fosse no WhatsApp…"):
    try:
        api_enviar(lead_id, texto)
    except (RuntimeError, httpx.HTTPError) as erro:
        st.error(f"Não foi possível enviar: {erro}")


@st.fragment(run_every=POLLING_SEGUNDOS)
def conversa(lead_id: str) -> None:
    """Reexecuta sozinho a cada POLLING_SEGUNDOS — só esta área, o input segue livre."""
    try:
        historico = api_get(f"/conversas/{lead_id}/mensagens")
    except httpx.HTTPError as erro:
        st.warning(f"Sem conexão com a API: {erro}")
        return
    for mensagem in historico["mensagens"]:
        papel = "user" if mensagem["papel"] == "lead" else "assistant"
        with st.chat_message(papel, avatar="🙂" if papel == "user" else "🏠"):
            st.markdown(mensagem["texto"])
            if mensagem["status"] == "falha":
                st.caption("⚠️ a Lia não conseguiu responder a esta mensagem")
            mostrar_itens(mensagem.get("itens_citados", []))
    if historico["processando"]:
        with st.chat_message("assistant", avatar="🏠"):
            st.markdown("_Lia está digitando…_")


conversa(lead_id)
