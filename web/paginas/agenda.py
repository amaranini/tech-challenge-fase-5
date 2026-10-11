"""Agenda: agendamentos do período por responsável e por dia."""

import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # raiz: pacote "web"

from web.comum import (
    ATUALIZACAO_SEGUNDOS,
    DIAS,
    MODALIDADES,
    TIPOS_AGENDAMENTO,
    api_get_seguro,
    barras,
    periodo_selecionado,
)  # fmt: skip

st.title("📅 Agenda")
periodo = periodo_selecionado()
incluir_cancelados = st.toggle("Incluir cancelados", value=False)


@st.fragment(run_every=ATUALIZACAO_SEGUNDOS)
def agenda(periodo: dict[str, str], cancelados: bool) -> None:
    todos: list[dict[str, Any]] = (
        api_get_seguro("/agendamentos", a_partir_de=periodo["inicio"], limite=500) or []
    )
    fim = datetime.fromisoformat(periodo["fim"])
    agendamentos = [
        a for a in todos
        if datetime.fromisoformat(a["inicio"]) < fim and (cancelados or a["status"] == "ativo")
    ]  # fmt: skip
    st.caption(f"{len(agendamentos)} agendamento(s) começando no período")
    if not agendamentos:
        return

    def dia(a: dict[str, Any]) -> str:
        local = datetime.fromisoformat(a["inicio"]).astimezone()
        return f"{local:%d/%m} {DIAS[local.weekday()]}"

    def hora(a: dict[str, Any]) -> str:
        return f"{datetime.fromisoformat(a['inicio']).astimezone():%H:%M}"

    c1, c2 = st.columns(2)
    with c1:
        st.subheader("Por responsável")
        por_resp = Counter(a["responsavel"]["nome"] for a in agendamentos)
        barras(
            [{"chave": k, "total": v} for k, v in por_resp.most_common()],
            rotulo_valor="Agendamentos",
        )
    with c2:
        st.subheader("Por dia")
        por_dia = Counter(dia(a) for a in sorted(agendamentos, key=lambda a: a["inicio"]))
        barras(
            [{"chave": k, "total": v} for k, v in por_dia.items()],
            rotulo_valor="Agendamentos",
            altura_barra=28,
        )

    st.subheader("Agendamentos")
    responsaveis = sorted({a["responsavel"]["nome"] for a in agendamentos})
    for aba, nome in zip(st.tabs(["Todos", *responsaveis]), [None, *responsaveis], strict=True):
        with aba:
            st.dataframe(
                [
                    {
                        "Quando": f"{dia(a)} {hora(a)}",
                        "Responsável": a["responsavel"]["nome"],
                        "Tipo": TIPOS_AGENDAMENTO.get(a["tipo"], a["tipo"]),
                        "Modalidade": MODALIDADES.get(a["modalidade"], a["modalidade"]),
                        "Lead": a["lead_id"],
                        "Status": a["status"],
                    }
                    for a in sorted(agendamentos, key=lambda a: a["inicio"])
                    if nome is None or a["responsavel"]["nome"] == nome
                ],
                hide_index=True,
                width="stretch",
            )


agenda(periodo, incluir_cancelados)
