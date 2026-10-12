"""O relatório traz resumo, tabela por cenário, falhas e a análise escrita à mão."""

from pathlib import Path

import pytest

from evals import metricas, relatorio
from evals.ancoragem import Ancoragem
from evals.juiz import Julgamento
from evals.runner import ResultadoCenario, TurnoAvaliado

CONTEXTO = {
    "inicio": "2026-10-12T12:00:00",
    "api": "http://t",
    "producao": False,
    "commit": "abc123",
    "juiz": "gpt-4.1",
    "rubrica": "rubrica_juiz_v1",
    "debounce_s": 5.0,
}


def _turno(**extra: object) -> TurnoAvaliado:
    base: dict[str, object] = {
        "fala": "oi",
        "resposta": "Oi! O que você procura?",
        "latencia_s": 8.0,
        "processamento_s": 7.0,
        "no_resposta": "descoberta",
        "modelo": "gpt-4.1-mini",
        "tokens_entrada": 1000,
        "tokens_saida": 100,
        "custo_usd": 0.0006,
        "trace_id": None,
        "intencao": None,
    }
    return TurnoAvaliado(**(base | extra))  # type: ignore[arg-type]


@pytest.fixture
def resultados() -> list[ResultadoCenario]:
    ok = ResultadoCenario("bom", "tudo certo", "eval-bom")
    ok.turnos = [_turno()]
    ok.intencao = metricas.Contagem(1, 1)
    ok.comportamentos = {"respeitou_opt_out": True}
    ok.juiz = Julgamento(
        dict.fromkeys(("naturalidade", "empatia", "clareza", "conducao", "tom_e_limites"), 4),
        dict.fromkeys(("naturalidade", "empatia", "clareza", "conducao", "tom_e_limites"), "ok"),
        "gpt-4.1",
        10,
        10,
    )
    ruim = ResultadoCenario("ruim", "falhou", "eval-ruim", erros=["intencao: esperado 'compra'"])
    ruim.turnos = [_turno(ancoragem=[Ancoragem("IMV-001", True, False, ["zona norte"])])]
    pulado = ResultadoCenario("demo", "usa demo", "eval-demo", pulado="usa /demo")
    return [ok, ruim, pulado]


def test_relatorio_completo(tmp_path: Path, resultados: list[ResultadoCenario]) -> None:
    destino = tmp_path / "relatorio.md"
    relatorio.escrever_relatorio(destino, resultados, CONTEXTO)
    texto = destino.read_text(encoding="utf-8")
    assert "| Cenários aprovados | 1/2 (1 pulado(s)) |" in texto
    assert "| Acurácia de intenção | 100% (1/1 pontos anotados) |" in texto
    assert "| **Juiz — média geral (1–5)** | **4.00** |" in texto
    assert "### ruim (`eval-ruim`)" in texto
    assert "ancoragem: IMV-001 — zona norte" in texto
    assert "⏭️ pulado (usa /demo)" in texto
    assert "## Análise" in texto
    assert "## Anexo — transcrições" in texto


def test_atualizar_so_a_analise(
    tmp_path: Path, resultados: list[ResultadoCenario], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(relatorio, "PASTA", tmp_path)
    destino = tmp_path / "relatorio.md"
    relatorio.escrever_relatorio(destino, resultados, CONTEXTO)
    (tmp_path / "analise.md").write_text("Análise nova.", encoding="utf-8")
    relatorio.atualizar_analise(destino)
    texto = destino.read_text(encoding="utf-8")
    assert "## Análise\n\nAnálise nova.\n\n## Anexo" in texto
    assert texto.count("## Resumo") == 1
