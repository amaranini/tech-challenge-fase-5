"""Visão geral: KPIs, funil de conversão e distribuições (intenção, score, canal)."""

import sys
from pathlib import Path
from typing import Any

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # raiz: pacote "web"

from web.comum import (
    ATUALIZACAO_SEGUNDOS,
    CANAIS_ROTULO,
    CLASSIFICACOES_ROTULO,
    ESTADOS_CURTOS,
    INTENCOES,
    api_get_seguro,
    barras,
    duracao,
    percentual,
    periodo_selecionado,
)  # fmt: skip

st.title("📊 Visão geral")
periodo = periodo_selecionado()
st.caption(
    "Funil e distribuições: leads criados no período (coorte). Handoffs e SLA: eventos "
    "do período. Atualiza sozinho."
)


@st.fragment(run_every=ATUALIZACAO_SEGUNDOS)
def visao(periodo: dict[str, str]) -> None:
    v: dict[str, Any] | None = api_get_seguro("/painel/visao-geral", **periodo)
    if v is None:
        return
    funil = v["funil"]
    conversao_total = v["agendados"] / v["novos"] if v["novos"] else None

    k = st.columns(4)
    k[0].metric("Novos leads", v["novos"])
    k[1].metric(
        "Qualificados",
        v["qualificados"],
        percentual(funil[2]["conversao_da_anterior"]),
        delta_color="off",
    )
    k[2].metric(
        "Agendados",
        v["agendados"],
        percentual(funil[3]["conversao_da_anterior"]),
        delta_color="off",
    )
    k[3].metric("Conversão novo → agendado", percentual(conversao_total))
    k = st.columns(4)
    k[0].metric("1ª resposta (mediana)", duracao(v["mediana_primeira_resposta_s"]))
    k[1].metric("Até qualificar (mediana)", duracao(v["mediana_ate_qualificar_s"]))
    k[2].metric("Handoffs para humano", v["handoffs"])
    k[3].metric("🚨 SLA da fila excedido", v["sla_excedidos"])

    st.subheader("Funil de conversão")
    barras(
        [
            {
                "chave": e["etapa"],
                "total": e["total"],
                "texto": f"{e['total']}"
                + (
                    f"  ({percentual(e['conversao_da_anterior'])} da etapa anterior)"
                    if e["conversao_da_anterior"] is not None
                    else ""
                ),
            }
            for e in funil
        ],
        rotulo_valor="Leads",
        altura_barra=42,
    )

    c1, c2 = st.columns(2)
    with c1:
        st.subheader("Por intenção")
        barras(
            v["por_intencao"],
            rotulos={**INTENCOES, "indefinida": "❔ indefinida"},
            rotulo_valor="Leads",
        )
        st.subheader("Por canal")
        barras(v["por_canal"], rotulos=CANAIS_ROTULO, rotulo_valor="Leads")
    with c2:
        st.subheader("Classificação do score")
        barras(v["por_classificacao"], rotulos=CLASSIFICACOES_ROTULO, rotulo_valor="Leads")
        st.subheader("Estado do atendimento (agora)")
        barras(v["por_estado_atendimento"], rotulos=ESTADOS_CURTOS, rotulo_valor="Leads")


visao(periodo)
