"""Leads: tabela filtrável (estado de atendimento, score, intenção, canal) e o detalhe do
lead escolhido — conversa com autor, ficha, score e motivos, timeline de eventos, resumo,
agendamentos, follow-ups e o link para o trace de cada resposta no Langfuse."""

import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # raiz: pacote "web"

from web.comum import (
    ACOES,
    AVATARES,
    CANAIS_ROTULO,
    CLASSIFICACOES_ROTULO,
    ESTADOS_CURTOS,
    INTENCOES,
    api_get_seguro,
    descrever_agendamento,
    descrever_evento,
    formatar_horario,
    md,
    mostrar_atendimento,
    mostrar_envio,
    mostrar_ficha,
    mostrar_itens,
    mostrar_resumo,
    mostrar_score,
    periodo_selecionado,
    protegido,
)  # fmt: skip

st.title("👥 Leads")
periodo = periodo_selecionado()

TODOS = "todos"
filtros = st.columns(6)
canal = filtros[0].selectbox(
    "Canal", [TODOS, "web", "whatsapp"], format_func=lambda c: CANAIS_ROTULO.get(c, c)
)
intencao = filtros[1].selectbox(
    "Intenção", [TODOS, *INTENCOES, "indefinida"], format_func=lambda i: INTENCOES.get(i, i)
)
estado = filtros[2].selectbox(
    "Atendimento", [TODOS, *ESTADOS_CURTOS], format_func=lambda e: ESTADOS_CURTOS.get(e, e)
)
classificacao = filtros[3].selectbox(
    "Classificação",
    [TODOS, *CLASSIFICACOES_ROTULO],
    format_func=lambda c: CLASSIFICACOES_ROTULO.get(c, c),
)
score_min, score_max = filtros[4].slider("Score", 0, 100, (0, 100))
origem = filtros[5].selectbox("Origem", [TODOS, "reais", "demo"])

params: dict[str, Any] = {**periodo, "limite": 500}
for nome, valor in (
    ("canal", canal),
    ("intencao", intencao),
    ("estado_atendimento", estado),
    ("classificacao", classificacao),
):
    if valor != TODOS:
        params[nome] = valor
if (score_min, score_max) != (0, 100):
    params |= {"score_min": score_min, "score_max": score_max}
if origem != TODOS:
    params["demo"] = str(origem == "demo").lower()

leads: list[dict[str, Any]] = api_get_seguro("/painel/leads", **params) or []
st.caption(f"{len(leads)} lead(s) criados no período · clique numa linha para ver o detalhe")


def _linha(lead: dict[str, Any]) -> dict[str, Any]:
    return {
        "Lead": protegido(
            f"{lead['nome']} · {lead['lead_id']}" if lead["nome"] else lead["lead_id"]
        ),
        "Canal": CANAIS_ROTULO.get(lead["canal"], lead["canal"]),
        "Intenção": INTENCOES.get(lead["intencao"] or "", "❔ indefinida"),
        "Score": lead["score"],
        "Classificação": CLASSIFICACOES_ROTULO.get(lead["classificacao"] or "", "—"),
        "Atendimento": ESTADOS_CURTOS.get(lead["estado_atendimento"], lead["estado_atendimento"]),
        "Agendado": "📅" if lead["agendado"] else "",
        "Opt-out": "🛑" if lead["opt_out"] else "",
        "Msgs": lead["total_mensagens"],
        "Última interação": formatar_horario(lead["ultima_interacao_em"])
        if lead["ultima_interacao_em"]
        else "—",
        "Criado": formatar_horario(lead["criado_em"]),
        "Demo": "demo" if lead["demo"] else "",
    }


selecao = st.dataframe(
    [_linha(lead) for lead in leads],
    hide_index=True,
    width="stretch",
    on_select="rerun",
    selection_mode="single-row",
    column_config={
        "Score": st.column_config.ProgressColumn("Score", min_value=0, max_value=100, format="%d")
    },
    key="tabela_leads",
)
linhas = selecao.selection.rows if selecao else []
if not linhas:
    st.stop()

lead_id: str = leads[linhas[0]]["lead_id"]
detalhe: dict[str, Any] | None = api_get_seguro(f"/leads/{lead_id}")
historico: dict[str, Any] | None = api_get_seguro(f"/conversas/{lead_id}/mensagens")
if detalhe is None or historico is None:
    st.stop()

st.divider()
st.header(protegido(f"{CANAIS_ROTULO.get(detalhe['canal'], '')} {lead_id}"))
traces = [m["trace_url"] for m in historico["mensagens"] if m.get("trace_url")]
if traces:
    st.link_button("🔎 Último trace no Langfuse", traces[-1])

conversa_col, dados_col = st.columns([3, 2], gap="large")
with conversa_col:
    st.subheader("💬 Conversa")
    for m in historico["mensagens"]:
        papel = "user" if m["papel"] == "lead" else "assistant"
        with st.chat_message(papel, avatar=AVATARES.get(m["papel"], "🏠")):
            autor = {"lead": "Lead", "assistente": "Lia (IA)"}.get(
                m["papel"], f"{m.get('responsavel') or 'Equipe'} (equipe)"
            )
            st.caption(f"{autor} · {formatar_horario(m['criada_em'])}")
            st.markdown(md(m["texto"]))
            if m["papel"] != "lead":
                mostrar_envio(m)
            mostrar_itens(m.get("itens_citados", []))

with dados_col:
    mostrar_atendimento(detalhe["atendimento"])
    intencao_lead = detalhe["intencao"]
    rotulo = INTENCOES.get(intencao_lead, intencao_lead) if intencao_lead else "❔ indefinida"
    st.markdown(f"**Intenção:** {rotulo}")
    mostrar_score(detalhe)
    if acao := detalhe["proxima_acao"]:
        st.success(f"**Próxima ação:** {ACOES.get(acao, acao)}")
    if intencao_lead:
        st.markdown(f"**Ficha de {intencao_lead}**")
        mostrar_ficha(detalhe["ficha"], detalhe["campos_faltantes"])

    st.subheader("📅 Agendamentos")
    agendamentos = api_get_seguro("/agendamentos", lead_id=lead_id, limite=50) or []
    for ag in agendamentos:
        situacao = "" if ag["status"] == "ativo" else f" · _{ag['status']}_"
        st.markdown(md(f"- {descrever_agendamento(ag)}{situacao}"))
    if not agendamentos:
        st.caption("Nenhum.")

    st.subheader("⏰ Follow-ups")
    if detalhe.get("opt_out_em"):
        st.caption(f"🛑 Opt-out em {formatar_horario(detalhe['opt_out_em'])}: sem mensagens ativas")
    for f in detalhe.get("followups", []):
        st.markdown(f"- {f['tipo']} · etapa {f['etapa']} · {formatar_horario(f['executar_em'])}")
    if not detalhe.get("followups"):
        st.caption("Nenhum programado.")

    mostrar_resumo(lead_id, recolhido=True)

st.subheader("🧭 Timeline de eventos")
for evento in reversed(detalhe["eventos"]):
    hora = datetime.fromisoformat(evento["ocorrido_em"]).astimezone().strftime("%d/%m %H:%M")
    st.markdown(md(f"`{hora}` {descrever_evento(evento)}"))
