"""Fila de atendimento humano (equipe): quem espera e há quanto tempo, SLA estourado em
destaque, resumo para quem vai atender, assumir, responder e devolver para a Lia."""

import sys
from pathlib import Path
from typing import Any

import httpx
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # raiz: pacote "web"

from web.autenticacao import somente_leitura
from web.comum import (
    CANAIS_ROTULO,
    api_get,
    api_post,
    md,
    minutos,
    mostrar_envio,
    mostrar_resumo,
    rotulo_lead,
)  # fmt: skip

st.title("🧑‍💼 Fila de atendimento")
responsavel = (
    st.text_input("Seu nome (quem está atendendo)", key="responsavel_nome").strip() or "Equipe"
)


def _cartao_aguardando(item: dict[str, Any], quem: str) -> None:
    lead = item["lead_id"]
    with st.container(border=True):
        colunas = st.columns([4, 3, 3, 2])
        colunas[0].markdown(f"**{rotulo_lead(item)}**")
        colunas[0].caption(CANAIS_ROTULO.get(item.get("canal", "web"), ""))
        espera = f"esperando há **{minutos(item['espera_segundos'])}**"
        if item.get("sla_excedido"):
            colunas[1].error(f"🚨 SLA excedido · {minutos(item['espera_segundos'])}")
        else:
            colunas[1].markdown(espera)
        colunas[2].markdown(f"{item['intencao'] or '—'} · score {item['score'] or '—'}")
        if somente_leitura():
            colunas[3].caption("somente leitura")
        elif colunas[3].button("Assumir", key=f"assumir-{lead}", type="primary") and (
            falha := api_post(f"/atendimentos/{lead}/assumir", {"responsavel": quem})
        ):
            st.error(falha)
        mostrar_resumo(lead, recolhido=True)


def _em_atendimento(item: dict[str, Any], quem: str) -> None:
    lead = item["lead_id"]
    with st.expander(
        f"{rotulo_lead(item)} — com {item['atendimento']['responsavel']}", expanded=True
    ):
        mostrar_resumo(lead, recolhido=False)
        for m in api_get(f"/conversas/{lead}/mensagens")["mensagens"][-12:]:
            autor = {"lead": "🙂 Lead", "assistente": "🏠 Lia"}.get(
                m["papel"], f"🧑‍💼 {m.get('responsavel') or 'Equipe'}"
            )
            st.markdown(md(f"**{autor}:** {m['texto']}"))
            if m["papel"] != "lead":
                mostrar_envio(m)
        if somente_leitura():
            return
        with st.form(f"responder-{lead}", clear_on_submit=True):
            texto = st.text_area("Responder ao lead", key=f"texto-{lead}")
            if st.form_submit_button("Enviar") and texto.strip():
                corpo = {"texto": texto, "responsavel": quem}
                if falha := api_post(f"/atendimentos/{lead}/mensagens", corpo):
                    st.error(falha)
        if st.button("↩️ Devolver para a Lia", key=f"devolver-{lead}") and (
            falha := api_post(f"/atendimentos/{lead}/devolver")
        ):
            st.error(falha)


@st.fragment(run_every=3)
def fila(quem: str) -> None:
    try:
        aguardando: list[dict[str, Any]] = api_get("/atendimentos/fila")
        em_atendimento: list[dict[str, Any]] = api_get("/atendimentos")
    except httpx.HTTPError as erro:
        st.warning(f"Sem conexão com a API: {erro}")
        return
    estourados = sum(1 for i in aguardando if i.get("sla_excedido"))
    k = st.columns(3)
    k[0].metric("Aguardando", len(aguardando))
    k[1].metric("SLA excedido", estourados)
    k[2].metric("Em atendimento", len(em_atendimento))

    st.subheader(f"⏳ Aguardando ({len(aguardando)})")
    if not aguardando:
        st.caption("Ninguém na fila.")
    for item in sorted(
        aguardando, key=lambda i: (not i.get("sla_excedido"), -i["espera_segundos"])
    ):
        _cartao_aguardando(item, quem)

    st.subheader(f"🧑‍💼 Em atendimento ({len(em_atendimento)})")
    for item in em_atendimento:
        _em_atendimento(item, quem)


fila(responsavel)
