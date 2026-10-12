"""Gera evals/relatorio.md a partir dos resultados do runner.

A seção "Análise" é escrita à mão em evals/analise.md (lida das falhas desta rodada) e
incluída no relatório; o resto é gerado.
"""

from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from evals import metricas
from evals.juiz import CRITERIOS

if TYPE_CHECKING:
    from evals.runner import ResultadoCenario

PASTA = Path(__file__).resolve().parent
ROTULOS_COMPORTAMENTO = {
    "confirmou_antes_do_handoff": "Confirmou antes do handoff",
    "confirmou_antes_de_voltar_para_ia": "Confirmou antes de voltar para a IA",
    "confirmou_antes_de_reservar": "Confirmou antes de reservar/remarcar/cancelar",
    "respeitou_opt_out": "Respeitou o opt-out",
    "injecao_contida": "Conteve a tentativa de injeção",
    "no_maximo_uma_pergunta": "No máximo uma pergunta por resposta",
}


def _pct(valor: float | None) -> str:
    return "—" if valor is None else f"{100 * valor:.0f}%"


def _num(valor: float | None, casas: int = 1, sufixo: str = "") -> str:
    return "—" if valor is None else f"{valor:.{casas}f}{sufixo}"


def _usd(valor: float | None) -> str:
    return "—" if valor is None else f"US$ {valor:.4f}"


def _resumo_geral(rs: Sequence["ResultadoCenario"], contexto: dict[str, Any]) -> list[str]:
    executados = [r for r in rs if r.pulado is None]
    intencao = metricas.Contagem()
    extracao = metricas.Extracao()
    for r in executados:
        intencao = intencao.somar(r.intencao)
        extracao = extracao.somar(r.extracao)
    turnos = [t for r in executados for t in r.turnos if t.resposta is not None]
    latencia = metricas.distribuicao([t.latencia_s for t in turnos])
    processamento = metricas.distribuicao(
        [t.processamento_s for t in turnos if t.processamento_s is not None]
    )
    custos = [r.custo_usd for r in executados if r.turnos]
    itens = [a for t in turnos for a in t.ancoragem]
    avaliados = [a for a in itens if a.adere is not None]
    linhas = [
        "## Resumo",
        "",
        "| Métrica | Resultado |",
        "|---|---|",
        f"| Cenários aprovados | {sum(r.ok for r in executados)}/{len(executados)}"
        + (f" ({len(rs) - len(executados)} pulado(s))" if len(rs) > len(executados) else "")
        + " |",
        f"| Acurácia de intenção | {_pct(intencao.taxa)} ({intencao.acertos}/{intencao.total} "
        "pontos anotados) |",
        f"| Extração da ficha — precisão | {_pct(extracao.precisao)} "
        f"({extracao.corretos}/{extracao.avaliados}) |",
        f"| Extração da ficha — cobertura | {_pct(extracao.cobertura)} "
        f"({extracao.corretos}/{extracao.esperados}) |",
        f"| Imóveis citados que existem | {_pct(_taxa(a.existe for a in itens))} "
        f"({sum(a.existe for a in itens)}/{len(itens)}) |",
        f"| Imóveis citados aderentes à ficha | {_pct(_taxa(bool(a.adere) for a in avaliados))} "
        f"({sum(bool(a.adere) for a in avaliados)}/{len(avaliados)} avaliados) |",
    ]
    por_comportamento: dict[str, list[bool]] = defaultdict(list)
    for r in executados:
        for nome, ok in r.comportamentos.items():
            if ok is not None:
                por_comportamento[nome].append(ok)
    for nome, rotulo in ROTULOS_COMPORTAMENTO.items():
        valores = por_comportamento.get(nome, [])
        if valores:
            linhas.append(f"| {rotulo} | {sum(valores)}/{len(valores)} cenários |")
    julgados = [r.juiz for r in executados if r.juiz is not None]
    if julgados:
        for c in CRITERIOS:
            media = sum(j.notas[c] for j in julgados) / len(julgados)
            linhas.append(f"| Juiz — {c} (1–5) | {media:.2f} |")
        geral = sum(j.media for j in julgados) / len(julgados)
        linhas.append(f"| **Juiz — média geral (1–5)** | **{geral:.2f}** |")
    debounce = contexto.get("debounce_s", 5)
    linhas += [
        f"| Latência percebida por resposta (mediana · p95) | {_num(latencia.mediana, sufixo=' s')}"
        f" · {_num(latencia.p95, sufixo=' s')} (inclui o debounce de {debounce:.0f} s) |",
        f"| Processamento após a última fala (mediana · p95) | "
        f"{_num(processamento.mediana, sufixo=' s')} · {_num(processamento.p95, sufixo=' s')} |",
        f"| Custo médio por conversa (LLM da Lia) | {_usd(_media(custos))} |",
        f"| Custo médio por resposta | {_usd(_media([t.custo_usd for t in turnos if t.custo_usd]))}"
        " |",
        f"| Tokens médios por resposta (entrada · saída) | "
        f"{_num(_media([t.tokens_entrada for t in turnos]), 0)} · "
        f"{_num(_media([t.tokens_saida for t in turnos]), 0)} |",
    ]
    return linhas


def _taxa(valores: Any) -> float | None:
    lista = list(valores)
    return sum(lista) / len(lista) if lista else None


def _media(valores: Sequence[float]) -> float | None:
    return sum(valores) / len(valores) if valores else None


def _tabela_cenarios(rs: Sequence["ResultadoCenario"]) -> list[str]:
    linhas = [
        "## Por cenário",
        "",
        "| Cenário | Resultado | Intenção | Extração P · C | Comportamentos | Ancoragem | "
        "Juiz | Latência média | Custo |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rs:
        if r.pulado:
            linhas.append(f"| {r.nome} | ⏭️ pulado ({r.pulado}) | | | | | | | |")
            continue
        aplicaveis = {k: v for k, v in r.comportamentos.items() if v is not None}
        falhos = [ROTULOS_COMPORTAMENTO.get(k, k) for k, v in aplicaveis.items() if not v]
        comportamento = "—" if not aplicaveis else "✅" if not falhos else "❌ " + "; ".join(falhos)
        itens = [a for t in r.turnos for a in t.ancoragem]
        ancoragem = (
            "—"
            if not itens
            else f"{sum(a.existe for a in itens)}/{len(itens)} existem · "
            f"{sum(bool(a.adere) for a in itens)}/{sum(a.adere is not None for a in itens)} "
            "aderem"
        )
        latencias = [t.latencia_s for t in r.turnos if t.resposta]
        linhas.append(
            f"| {r.nome} | {'✅' if r.ok else '❌'} | {_pct(r.intencao.taxa)} | "
            f"{_pct(r.extracao.precisao)} · {_pct(r.extracao.cobertura)} | {comportamento} | "
            f"{ancoragem} | {_num(r.juiz.media if r.juiz else None, 2)} | "
            f"{_num(_media(latencias), sufixo=' s')} | {_usd(r.custo_usd)} |"
        )
    return linhas


def _falhas(rs: Sequence["ResultadoCenario"]) -> list[str]:
    linhas = ["## Falhas encontradas", ""]
    algum = False
    for r in rs:
        itens: list[str] = list(r.erros)
        itens += [
            f"ancoragem: {a.codigo} — {'; '.join(a.motivos)} (fala {t.fala!r})"
            for t in r.turnos
            for a in t.ancoragem
            if not a.existe or a.adere is False
        ]
        itens += [f"extração: {d}" for d in r.extracao.divergencias]
        if r.comportamentos.get("no_maximo_uma_pergunta") is False:
            itens.append("condução: alguma resposta fez mais de uma pergunta")
        if r.juiz is not None:
            itens += [
                f"juiz ({c} = {n}): {r.juiz.justificativas[c]}"
                for c, n in r.juiz.notas.items()
                if n < 4  # noqa: PLR2004
            ]
        if itens:
            algum = True
            linhas.append(f"### {r.nome} (`{r.lead_id}`)")
            linhas += [f"- {i}" for i in itens]
            linhas.append("")
    if not algum:
        linhas.append("Nenhuma falha nesta rodada.")
    return linhas


def _transcricoes(rs: Sequence["ResultadoCenario"]) -> list[str]:
    linhas = ["## Anexo — transcrições", ""]
    for r in rs:
        if r.pulado:
            continue
        linhas += [f"<details><summary>{r.nome} — {r.descricao}</summary>", ""]
        for t in r.turnos:
            linhas.append(f"- **Lead:** {t.fala}")
            if t.resposta:
                resposta = " ".join(t.resposta.split())
                linhas.append(f"  - **Lia** ({t.no_resposta}, {t.latencia_s:.1f} s): {resposta}")
        linhas += ["", "</details>", ""]
    return linhas


def escrever_relatorio(
    destino: Path, rs: Sequence["ResultadoCenario"], contexto: dict[str, Any]
) -> None:
    modelos = sorted({t.modelo for r in rs for t in r.turnos if t.modelo})
    analise = PASTA / "analise.md"
    linhas = [
        "# Relatório de avaliação — Lia (SDR imobiliário)",
        "",
        f"- **Rodada:** {contexto['inicio']} · commit `{contexto['commit']}`",
        f"- **API:** {contexto['api']}"
        + (" (produção: cenários com /demo pulados)" if contexto["producao"] else ""),
        f"- **Modelos da Lia:** {', '.join(modelos) or '—'} · **juiz:** "
        f"{contexto['juiz'] or 'desligado'} (`{contexto['rubrica']}.md`)",
        f"- **Cenários:** {len(rs)} roteiros de `evals/cenarios/` "
        "(definições das métricas em `evals/README.md`)",
        "",
        "Gerado por `uv run python -m evals.runner` — não edite à mão (a análise fica em "
        "`evals/analise.md`).",
        "",
        *_resumo_geral(rs, contexto),
        "",
        *_tabela_cenarios(rs),
        "",
        *_falhas(rs),
        "",
        "## Análise",
        "",
        analise.read_text(encoding="utf-8").strip()
        if analise.exists()
        else "_(escreva a análise em evals/analise.md)_",
        "",
        *_transcricoes(rs),
    ]
    destino.write_text("\n".join(linhas) + "\n", encoding="utf-8")


def atualizar_analise(relatorio: Path = PASTA / "relatorio.md") -> None:
    """Reescreve só a seção "Análise" do relatório com evals/analise.md (sem nova rodada)."""
    texto = relatorio.read_text(encoding="utf-8")
    inicio = texto.index("## Análise\n") + len("## Análise\n")
    fim = texto.index("## Anexo — transcrições")
    analise = (PASTA / "analise.md").read_text(encoding="utf-8").strip()
    relatorio.write_text(f"{texto[:inicio]}\n{analise}\n\n{texto[fim:]}", encoding="utf-8")


if __name__ == "__main__":
    atualizar_analise()
    print("Análise atualizada em evals/relatorio.md")


def metricas_resumidas(rs: Sequence["ResultadoCenario"]) -> dict[str, Any]:
    """Números principais da rodada, para gravar na API (card do eval offline no dashboard)."""
    executados = [r for r in rs if r.pulado is None]
    intencao = metricas.Contagem()
    extracao = metricas.Extracao()
    for r in executados:
        intencao = intencao.somar(r.intencao)
        extracao = extracao.somar(r.extracao)
    turnos = [t for r in executados for t in r.turnos if t.resposta is not None]
    itens = [a for t in turnos for a in t.ancoragem]
    avaliados = [a for a in itens if a.adere is not None]
    latencia = metricas.distribuicao([t.latencia_s for t in turnos])
    comportamentos: dict[str, str] = {}
    for nome in ROTULOS_COMPORTAMENTO:
        valores = [
            r.comportamentos[nome] for r in executados if r.comportamentos.get(nome) is not None
        ]
        if valores:
            comportamentos[nome] = f"{sum(bool(v) for v in valores)}/{len(valores)}"
    julgados = [r.juiz for r in executados if r.juiz is not None]
    custos = [r.custo_usd for r in executados if r.turnos]
    return {
        "cenarios_aprovados": sum(r.ok for r in executados),
        "cenarios_executados": len(executados),
        "cenarios_pulados": len(rs) - len(executados),
        "falharam": [r.nome for r in executados if not r.ok],
        "acuracia_intencao": intencao.taxa,
        "extracao_precisao": extracao.precisao,
        "extracao_cobertura": extracao.cobertura,
        "itens_existem": _taxa(a.existe for a in itens),
        "itens_aderem": _taxa(bool(a.adere) for a in avaliados),
        "comportamentos": comportamentos,
        "respostas_com_duas_perguntas": _taxa(
            t.resposta.count("?") > 1 for t in turnos if t.resposta
        ),
        "juiz": {c: sum(j.notas[c] for j in julgados) / len(julgados) for c in CRITERIOS}
        if julgados
        else {},
        "juiz_media": sum(j.media for j in julgados) / len(julgados) if julgados else None,
        "latencia_p50_s": latencia.mediana,
        "latencia_p95_s": latencia.p95,
        "custo_por_conversa_usd": _media(custos),
    }
