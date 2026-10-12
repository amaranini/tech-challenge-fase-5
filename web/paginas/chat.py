"""Chat com a Lia (como o lead) + painel de qualificação ao lado.

Chat e painel são fragments com polling (`run_every`): só essas áreas se redesenham, e o
campo de mensagem fica livre.
"""

import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # raiz: pacote "web"

from web.comum import (
    ACOES,
    API_URL,
    AVATARES,
    EVENTOS_NO_PAINEL,
    INTENCOES,
    POLLING_SEGUNDOS,
    TIMEOUT,
    api_enviar,
    api_get,
    cabecalhos,
    descrever_agendamento,
    descrever_evento,
    md,
    mostrar_atendimento,
    mostrar_envio,
    mostrar_ficha,
    mostrar_followup,
    mostrar_itens,
    mostrar_score,
    novo_lead_id,
    rotulo_lead,
)  # fmt: skip

# ------------------------------------------------------------------ barra lateral: leads
with st.sidebar:
    st.header("Leads")
    try:
        leads: list[dict[str, Any]] = api_get("/leads")
    except httpx.HTTPError as erro:
        st.error(f"API indisponível em {API_URL}: {erro}")
        st.stop()

    if st.button("➕ Novo lead", width="stretch"):
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
        lead["lead_id"]: f"{rotulo_lead(lead)} ({lead['total_mensagens']} msgs)" for lead in leads
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


lead_id: str = st.session_state.lead_id
do_whatsapp = lead_id.startswith("whatsapp:")
st.title("🏠 Lia — consultora da imobiliária")
if do_whatsapp:
    st.caption(
        f"📱 Conversa do WhatsApp (**{lead_id}**) — só leitura aqui; o lead escreve pelo "
        "celular e a equipe responde pela tela Fila."
    )
else:
    st.caption(
        f"Conversando como **{lead_id}** · pode mandar várias mensagens seguidas: a Lia "
        "espera você terminar e responde ao conjunto · reabra este lead_id para continuar"
    )

# O envio só registra a mensagem (HTTP 202); a resposta chega pelo polling abaixo.
if not do_whatsapp and (texto := st.chat_input("Escreva como se fosse no WhatsApp…")):
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
        with st.chat_message(papel, avatar=AVATARES.get(mensagem["papel"], "🏠")):
            if mensagem["papel"] == "responsavel":
                st.caption(f"{mensagem.get('responsavel') or 'Equipe'} · atendimento humano")
            st.markdown(md(mensagem["texto"]))
            if mensagem["status"] == "falha":
                st.caption("⚠️ a Lia não conseguiu responder a esta mensagem")
            if mensagem["papel"] != "lead":
                mostrar_envio(mensagem)
            mostrar_itens(mensagem.get("itens_citados", []))
    if historico["processando"]:
        with st.chat_message("assistant", avatar="🏠"):
            st.markdown("_Lia está digitando…_")


@st.fragment(run_every=POLLING_SEGUNDOS)
def painel(lead_id: str) -> None:
    """Qualificação em tempo real (GET /leads/{lead_id}): intenção, ficha, score, ação."""
    st.subheader("📋 Qualificação")
    try:
        resposta = httpx.get(f"{API_URL}/leads/{lead_id}", headers=cabecalhos(), timeout=TIMEOUT)
    except httpx.HTTPError as erro:
        st.warning(f"Sem conexão com a API: {erro}")
        return
    if resposta.status_code == httpx.codes.NOT_FOUND:
        st.info("Envie a primeira mensagem para começar a qualificação.")
        return
    if resposta.is_error:
        st.warning(f"Erro ao consultar o lead: {resposta.status_code}")
        return
    lead: dict[str, Any] = resposta.json()

    intencao = lead["intencao"]
    rotulo = INTENCOES.get(intencao, intencao) if intencao else "❔ indefinida"
    mostrar_atendimento(lead["atendimento"])
    st.markdown(f"**Intenção:** {rotulo}")

    mostrar_score(lead)

    if acao := lead["proxima_acao"]:
        st.success(f"**Próxima ação:** {ACOES.get(acao, acao)}")
    elif intencao:
        st.caption("Próxima ação: definida quando os dados essenciais estiverem na ficha")
    if agendamento := lead.get("agendamento"):
        st.info(md(f"📅 {descrever_agendamento(agendamento)}"))
    mostrar_followup(lead)

    if intencao:
        st.markdown(f"**Ficha de {intencao}**")
        mostrar_ficha(lead["ficha"], lead["campos_faltantes"])
    for outra, ficha in lead["fichas"].items():
        if outra != intencao and ficha:
            with st.expander(f"Ficha de {outra} (preservada)"):
                mostrar_ficha(ficha, [])

    eventos = lead["eventos"]
    with st.expander(f"Trilha de eventos ({len(eventos)})"):
        for evento in reversed(eventos[-EVENTOS_NO_PAINEL:]):
            hora = datetime.fromisoformat(evento["ocorrido_em"]).astimezone().strftime("%H:%M:%S")
            st.markdown(md(f"`{hora}` {descrever_evento(evento)}"))


col_chat, col_painel = st.columns([3, 2], gap="large")
with col_chat:
    conversa(lead_id)
with col_painel:
    painel(lead_id)
