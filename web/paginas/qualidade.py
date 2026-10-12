"""Qualidade da IA: o último eval offline (roteiros de evals/) e as métricas online das
conversas do período, por versão do agente (persona · modelo · prompts do core), com
links para o Langfuse nos detalhes técnicos. Só via API."""

import sys
from pathlib import Path
from typing import Any

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # raiz: pacote "web"

from web.comum import (
    ATUALIZACAO_SEGUNDOS,
    CAMPOS,
    api_get_seguro,
    barras,
    duracao,
    formatar_horario,
    percentual,
    periodo_selecionado,
    protegido,
)  # fmt: skip

COMPORTAMENTOS = {
    "confirmou_antes_do_handoff": "Confirmou antes do handoff",
    "confirmou_antes_de_voltar_para_ia": "Confirmou antes de voltar para a IA",
    "confirmou_antes_de_reservar": "Confirmou antes de reservar",
    "respeitou_opt_out": "Respeitou o opt-out",
    "injecao_contida": "Conteve a injeção",
    "no_maximo_uma_pergunta": "No máximo 1 pergunta por resposta",
}
TIPOS_INCIDENTE = {
    "TentativaInjecaoDetectada": "🛡️ Tentativa de injeção",
    "RespostaReprovadaNaValidacao": "🧯 Resposta reprovada",
}


def _usd(valor: float | None, casas: int = 4) -> str:
    return "—" if valor is None else f"US$ {valor:.{casas}f}"


def _numero(valor: float | None, casas: int = 1) -> str:
    return "—" if valor is None else f"{valor:.{casas}f}"


def _ponto(ponto: str) -> str:
    """'campo: preco_max' → 'campo: Preço máximo' (vocabulário da vertical na tela)."""
    if ponto.startswith("campo: "):
        campo = ponto.removeprefix("campo: ")
        return f"pergunta sobre {CAMPOS.get(campo, campo)}"
    return ponto


st.title("🧪 Qualidade da IA")
periodo = periodo_selecionado()
with st.sidebar:
    abandono_horas = st.slider(
        "Abandono: sem resposta há (horas)",
        1,
        72,
        24,
        help="O lead conta como abandono se a última fala é da Lia e ele não responde há "
        "esse tempo (sem opt-out, agendamento ou atendimento humano).",
    )
    apenas_reais = st.toggle("Só conversas reais (sem demo)", value=False)

versao_atual: dict[str, Any] | None = api_get_seguro("/qualidade/versao")


# ------------------------------------------------------------------ eval offline
def card_offline() -> None:
    st.subheader("Eval offline (roteiros)")
    rodadas: list[dict[str, Any]] | None = api_get_seguro("/qualidade/offline", limite=10)
    if rodadas is None:
        return
    if not rodadas:
        st.info("Nenhuma rodada registrada ainda: rode `make eval` (o runner grava aqui).")
        return
    ultima = rodadas[0]
    m, v, ctx = ultima["metricas"], ultima["versao"], ultima["contexto"]
    with st.container(border=True):
        st.markdown(
            f"**Última rodada:** {formatar_horario(ultima['executada_em'])} · "
            f"commit `{ctx.get('commit', '?')}` · juiz `{ctx.get('juiz') or 'desligado'}`"
        )
        modelos = v.get("modelos") or {}
        st.caption(
            f"Versão avaliada: persona `{v.get('persona', '?')}` · modelos "
            + ", ".join(f"{no} `{modelo}`" for no, modelo in modelos.items())
        )
        if versao_atual and {k: v.get(k) for k in ("persona", "prompts_core", "modelos")} != {
            k: versao_atual.get(k) for k in ("persona", "prompts_core", "modelos")
        }:
            st.warning("A versão em produção mudou desde este eval: rode `make eval` de novo.")
        k = st.columns(4)
        k[0].metric(
            "Cenários aprovados", f"{m.get('cenarios_aprovados')}/{m.get('cenarios_executados')}"
        )
        k[1].metric("Acurácia de intenção", percentual(m.get("acuracia_intencao")))
        k[2].metric(
            "Extração (precisão · cobertura)",
            f"{percentual(m.get('extracao_precisao'))} · {percentual(m.get('extracao_cobertura'))}",
        )
        k[3].metric("Imóveis citados aderentes", percentual(m.get("itens_aderem")))
        k = st.columns(4)
        k[0].metric("Juiz (1–5)", _numero(m.get("juiz_media"), 2))
        k[1].metric("Latência p50 · p95", f"{duracao(m.get('latencia_p50_s'))} · "
                    f"{duracao(m.get('latencia_p95_s'))}")  # fmt: skip
        k[2].metric("Custo por conversa", _usd(m.get("custo_por_conversa_usd")))
        k[3].metric("Respostas com 2 perguntas", percentual(m.get("respostas_com_duas_perguntas")))
        comportamentos = m.get("comportamentos") or {}
        if comportamentos:
            st.caption(
                "Comportamentos obrigatórios: "
                + " · ".join(f"{COMPORTAMENTOS.get(n, n)} {r}" for n, r in comportamentos.items())
            )
        if m.get("falharam"):
            st.caption(f"Falharam: {', '.join(m['falharam'])} — análise em `evals/relatorio.md`.")
        if m.get("juiz"):
            st.caption(
                "Juiz por critério: " + " · ".join(f"{c} {n:.2f}" for c, n in m["juiz"].items())
            )
    if len(rodadas) > 1:
        with st.expander(f"Histórico ({len(rodadas)} rodadas)"):
            st.dataframe(
                [
                    {
                        "quando": formatar_horario(r["executada_em"]),
                        "persona": r["versao"].get("persona"),
                        "modelo (agente)": (r["versao"].get("modelos") or {}).get("agente"),
                        "aprovados": f"{r['metricas'].get('cenarios_aprovados')}/"
                        f"{r['metricas'].get('cenarios_executados')}",
                        "juiz": _numero(r["metricas"].get("juiz_media"), 2),
                        "2 perguntas": percentual(
                            r["metricas"].get("respostas_com_duas_perguntas")
                        ),
                        "custo/conversa": _usd(r["metricas"].get("custo_por_conversa_usd")),
                        "commit": r["contexto"].get("commit"),
                    }
                    for r in rodadas
                ],
                hide_index=True,
                width="stretch",
            )


# ------------------------------------------------------------------ online
@st.fragment(run_every=ATUALIZACAO_SEGUNDOS)
def online(periodo: dict[str, str], abandono_horas: int, apenas_reais: bool) -> None:
    st.subheader("Conversas reais (online)")
    q: dict[str, Any] | None = api_get_seguro(
        "/qualidade/online",
        **periodo,
        abandono_horas=abandono_horas,
        apenas_reais=str(apenas_reais).lower(),
    )
    if q is None:
        return
    projeto = q.get("langfuse_projeto_url")
    st.caption(
        "Leads criados no período; cada conversa conta na versão do agente com que começou."
        + (f" [🔎 Abrir o projeto no Langfuse]({projeto})" if projeto else "")
    )
    versoes = q["versoes"]
    if not versoes:
        st.info("Sem conversas no período.")
        return
    escolhida = st.selectbox(
        "Versão do agente",
        [v["versao"] for v in versoes],
        format_func=lambda chave: next(
            f"{chave}  ({v['conversas']} conversas)" for v in versoes if v["versao"] == chave
        ),
        help="persona · modelo do agente · hash dos prompts do core",
    )
    v = next(x for x in versoes if x["versao"] == escolhida)
    if v["prompts_core"]:
        st.caption(f"Prompts do core: `{v['prompts_core']}`")

    k = st.columns(4)
    k[0].metric("Conversas", v["conversas"])
    k[1].metric("Taxa de qualificação", percentual(v["taxa_qualificacao"]))
    k[2].metric("Pediram humano", percentual(v["taxa_pedido_humano"]))
    k[3].metric("Opt-out", percentual(v["taxa_opt_out"]))
    k = st.columns(4)
    k[0].metric("Abandono", percentual(v["taxa_abandono"]))
    k[1].metric("Turnos até qualificar", _numero(v["turnos_ate_qualificar_medio"]))
    k[2].metric("Tempo até qualificar", duracao(v["segundos_ate_qualificar_medio"]))
    k[3].metric("Agendamentos", v["agendamentos"])
    k = st.columns(4)
    k[0].metric(
        "Latência p50 · p95",
        f"{duracao(v['latencia_p50_s'])} · {duracao(v['latencia_p95_s'])}",
        help="Resposta − última fala do lead (inclui o debounce).",
    )
    k[1].metric("Custo por conversa", _usd(v["custo_por_conversa_usd"]))
    k[2].metric("Custo por agendamento", _usd(v["custo_por_agendamento_usd"]))
    k[3].metric(
        "Respostas bloqueadas · reprovadas",
        f"{v['respostas_bloqueadas']} · {v['respostas_reprovadas']}",
        help=f"de {v['respostas']} respostas. Reprovada = corrigida na 2ª geração; "
        "bloqueada = saiu a resposta segura.",
    )

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Onde o lead parou de responder** (abandonos)")
        barras(
            [
                {"chave": _ponto(a["ponto"]), "total": a["total"]}
                for a in q["abandonos"]
                if a["versao"] == escolhida
            ],
            rotulo_valor="Abandonos",
        )
    with c2:
        st.markdown("**Guardrails**")
        st.metric("Tentativas de injeção contidas", v["tentativas_injecao"])
        st.metric(
            "Resumos avaliados úteis (equipe)",
            percentual(q["taxa_resumo_util"]),
            f"{q['resumos_avaliados']} avaliação(ões)",
            delta_color="off",
        )

    incidentes = q["incidentes"]
    with st.expander(f"Incidentes recentes ({len(incidentes)})", expanded=False):
        if not incidentes:
            st.caption("Nenhum no período.")
        for i in incidentes:
            links = " · ".join(
                f"[{rotulo}]({url})"
                for rotulo, url in (("🔎 rastro", i["trace_url"]), ("sessão", i["sessao_url"]))
                if url
            )
            tipo = TIPOS_INCIDENTE.get(i["tipo"], i["tipo"])
            st.markdown(
                f"{tipo} · {formatar_horario(i['ocorrido_em'])} · `{protegido(i['lead_id'])}` — "
                f"{i['detalhe']}" + (f" · {links}" if links else "")
            )

    with st.expander("Todas as versões"):
        st.dataframe(
            [
                {
                    "versão": x["versao"],
                    "conversas": x["conversas"],
                    "qualificação": percentual(x["taxa_qualificacao"]),
                    "humano": percentual(x["taxa_pedido_humano"]),
                    "opt-out": percentual(x["taxa_opt_out"]),
                    "abandono": percentual(x["taxa_abandono"]),
                    "latência p50": duracao(x["latencia_p50_s"]),
                    "custo/conversa": _usd(x["custo_por_conversa_usd"]),
                    "bloqueadas": x["respostas_bloqueadas"],
                    "injeções": x["tentativas_injecao"],
                }
                for x in versoes
            ],
            hide_index=True,
            width="stretch",
        )


card_offline()
online(periodo, abandono_horas, apenas_reais)
