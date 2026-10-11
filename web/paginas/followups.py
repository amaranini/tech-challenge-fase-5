"""Follow-ups: pendentes agora e, no período, enviados, reengajamento e opt-outs."""

import sys
from pathlib import Path
from typing import Any

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # raiz: pacote "web"

from web.comum import (
    ATUALIZACAO_SEGUNDOS,
    api_get_seguro,
    formatar_horario,
    percentual,
    periodo_selecionado,
)  # fmt: skip

st.title("⏰ Follow-ups")
periodo = periodo_selecionado()

TIPOS_EVENTO = {
    "FollowUpEnviado": "📨 enviado",
    "LeadReengajado": "🔁 reengajou",
    "LeadOptOut": "🛑 opt-out",
    "LeadEncerradoPorInatividade": "💤 encerrado por inatividade",
}


@st.fragment(run_every=ATUALIZACAO_SEGUNDOS)
def followups(periodo: dict[str, str]) -> None:
    f: dict[str, Any] | None = api_get_seguro("/painel/followups", **periodo)
    if f is None:
        return
    k = st.columns(5)
    k[0].metric("Enviados", f["enviados"])
    k[1].metric("Por template (fora de 24h)", f["enviados_por_template"])
    k[2].metric(
        "Reengajamento",
        percentual(f["taxa_reengajamento"]),
        f"{f['reengajados']} de {f['leads_com_followup']} leads",
        delta_color="off",
    )
    k[3].metric("Opt-outs", f["opt_outs"])
    k[4].metric("Encerrados por inatividade", f["encerrados_por_inatividade"])

    st.subheader(f"Pendentes ({len(f['pendentes'])})")
    if f["pendentes"]:
        st.dataframe(
            [
                {
                    "Quando": formatar_horario(p["executar_em"]),
                    "Lead": p["lead_id"],
                    "Tipo": p["tipo"].replace("_", " "),
                    "Etapa": p["etapa"],
                    "Status": p["status"],
                }
                for p in f["pendentes"]
            ],
            hide_index=True,
            width="stretch",
        )
    else:
        st.caption("Nenhum follow-up programado.")

    st.subheader("Últimos no período")
    if f["recentes"]:
        st.dataframe(
            [
                {
                    "Quando": formatar_horario(e["ocorrido_em"]),
                    "Lead": e["lead_id"],
                    "O que aconteceu": TIPOS_EVENTO.get(e["tipo"], e["tipo"]),
                    "Detalhe": e["detalhe"] or "",
                }
                for e in f["recentes"]
            ],
            hide_index=True,
            width="stretch",
        )
    else:
        st.caption("Nada no período.")


followups(periodo)
