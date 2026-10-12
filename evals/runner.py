"""Runner da avaliação: executa os roteiros de evals/cenarios/ contra a API no ar (local
ou nuvem), mede e gera evals/relatorio.md (+ evals/resultados.json).

Uso:
    uv run python -m evals.runner                       # todos, API local
    uv run python -m evals.runner --api https://...     # contra o deploy
    uv run python -m evals.runner -k injecao --sem-juiz
    make eval

Custa tokens (LLM real da Lia + juiz). Precisa da API no ar; os cenários de follow-up
usam /demo (não existe em produção: na nuvem eles são pulados) e o worker.
"""

import argparse
import asyncio
import json
import logging
import os
import subprocess
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evals import metricas
from evals.ancoragem import Ancoragem, avaliar_item
from evals.cenarios import Cenario, carregar_cenarios, divergencias, divergencias_resumo
from evals.cliente import ApiViva
from evals.juiz import VERSAO_RUBRICA, Juiz, Julgamento

PASTA = Path(__file__).resolve().parent
logger = logging.getLogger("evals")


@dataclass
class TurnoAvaliado:
    fala: str
    resposta: str | None
    latencia_s: float
    processamento_s: float | None  # resposta − última fala do lote (inclui o debounce)
    no_resposta: str | None
    modelo: str | None
    tokens_entrada: int
    tokens_saida: int
    custo_usd: float | None
    trace_id: str | None
    intencao: str | None  # estado do lead depois do turno
    intencao_esperada: object = None
    ancoragem: list[Ancoragem] = field(default_factory=list)


@dataclass
class ResultadoCenario:
    nome: str
    descricao: str
    lead_id: str
    erros: list[str] = field(default_factory=list)
    pulado: str | None = None
    turnos: list[TurnoAvaliado] = field(default_factory=list)
    intencao: metricas.Contagem = field(default_factory=metricas.Contagem)
    extracao: metricas.Extracao = field(default_factory=metricas.Extracao)
    comportamentos: dict[str, bool | None] = field(default_factory=dict)
    juiz: Julgamento | None = None
    duracao_s: float = 0.0

    @property
    def ok(self) -> bool:
        return not self.erros and self.pulado is None

    @property
    def custo_usd(self) -> float:
        return sum(t.custo_usd or 0 for t in self.turnos)


def _precos() -> dict[str, tuple[float, float]]:
    """Tabela padrão + EVAL_PRECOS='{"modelo": [entrada, saida]}' (US$ / 1M tokens)."""
    extra = json.loads(os.environ.get("EVAL_PRECOS", "{}"))
    return metricas.PRECOS_POR_MILHAO | {k: (float(v[0]), float(v[1])) for k, v in extra.items()}


def _usa_demo(cenario: Cenario) -> bool:
    return any(f.acao and f.acao.get("acao") == "simular_inatividade" for f in cenario.falas)


class Runner:
    def __init__(self, api: ApiViva, *, juiz: Juiz | None, producao: bool) -> None:
        self.api = api
        self.juiz = juiz
        self.producao = producao
        self.precos = _precos()
        self._itens: dict[str, dict[str, Any] | None] = {}

    def _item(self, codigo: str) -> dict[str, Any] | None:
        if codigo not in self._itens:
            self._itens[codigo] = self.api.item(codigo)
        return self._itens[codigo]

    def executar(self, cenario: Cenario) -> ResultadoCenario:
        lead_id = f"eval-{cenario.nome[:24]}-{uuid.uuid4().hex[:6]}"
        r = ResultadoCenario(cenario.nome, cenario.descricao, lead_id)
        if self.producao and _usa_demo(cenario):
            r.pulado = "usa /demo (desligado em produção)"
            return r
        inicio = time.monotonic()
        try:
            self._roteiro(cenario, r)
            self._final(cenario, r)
        except Exception as erro:
            logger.exception("cenário %s quebrou", cenario.nome)
            r.erros.append(f"execução: {type(erro).__name__}: {erro}")
        r.duracao_s = time.monotonic() - inicio
        return r

    def _roteiro(self, cenario: Cenario, r: ResultadoCenario) -> None:
        pontos_intencao: list[tuple[object, object]] = []
        for numero, fala in enumerate(cenario.falas, start=1):
            if fala.acao is not None:
                if fala.acao["acao"] == "conferir_resumo_na_fila":
                    self.api.aguardar_resumo(r.lead_id, None)
                else:
                    self.api.acao_da_equipe(r.lead_id, fala.acao)
                continue
            esperado = fala.esperado or {}
            turno = self.api.falar(r.lead_id, *fala.mensagens)
            estado = self.api.lead(r.lead_id)
            resposta = turno.resposta
            if esperado.get("sem_resposta"):
                if resposta is not None:  # a última mensagem deveria ser a do lead
                    r.erros.append(f"fala {numero}: a IA respondeu em atendimento humano")
                    resposta = None
            elif resposta is None:
                r.erros.append(f"fala {numero}: sem resposta da Lia")
            texto = (resposta or {}).get("texto", "")
            r.erros += [
                f"fala {numero}: resposta sem {t!r}"
                for t in esperado.get("resposta_contem", [])
                if t.casefold() not in texto.casefold()
            ] + [
                f"fala {numero}: resposta com {t!r}"
                for t in esperado.get("resposta_nao_contem", [])
                if t.casefold() in texto.casefold()
            ]
            r.erros += [f"fala {numero}: {e}" for e in divergencias(estado, esperado)]
            if "intencao" in esperado:
                pontos_intencao.append((estado.get("intencao"), esperado["intencao"]))
            r.turnos.append(self._turno(fala.texto, turno, resposta, estado, esperado))
        if "intencao" in cenario.esperado:
            pontos_intencao.append(
                (self.api.lead(r.lead_id).get("intencao"), cenario.esperado["intencao"])
            )
        r.intencao = metricas.acerto_intencao(pontos_intencao)

    def _turno(
        self,
        fala: str,
        turno: Any,
        resposta: dict[str, Any] | None,
        estado: dict[str, Any],
        esperado: dict[str, Any],
    ) -> TurnoAvaliado:
        modelo, entrada, saida = metricas.tokens_da_resposta(resposta) if resposta else (None, 0, 0)
        processamento = None
        if resposta is not None:
            fim = datetime.fromisoformat(resposta["criada_em"])
            processamento = (
                fim - datetime.fromisoformat(_ultima_fala(turno.historico))
            ).total_seconds()
        intencao = estado.get("intencao")
        ancoragem = [
            avaliar_item(c, self._item(c), intencao, estado.get("ficha", {}))
            for c in (metricas.codigos_citados(resposta) if resposta else [])
        ]
        return TurnoAvaliado(
            fala=fala,
            resposta=(resposta or {}).get("texto"),
            latencia_s=turno.latencia_s,
            processamento_s=processamento,
            no_resposta=((resposta or {}).get("diagnostico") or {})
            .get("agente", {})
            .get("no_resposta"),
            modelo=modelo,
            tokens_entrada=entrada,
            tokens_saida=saida,
            custo_usd=metricas.custo_usd(modelo, entrada, saida, self.precos),
            trace_id=(resposta or {}).get("trace_id"),
            intencao=intencao,
            intencao_esperada=esperado.get("intencao"),
            ancoragem=ancoragem,
        )

    def _final(self, cenario: Cenario, r: ResultadoCenario) -> None:
        estado = self.api.lead(r.lead_id)
        mensagens = self.api.historico(r.lead_id)["mensagens"]
        r.erros += divergencias(estado, cenario.esperado)
        if esperado_resumo := cenario.esperado.get("resumo"):
            resumo = self.api.aguardar_resumo(r.lead_id, esperado_resumo.get("gatilho"))
            r.erros += divergencias_resumo(resumo, esperado_resumo, mensagens)
        r.extracao = metricas.extracao_do_cenario(estado, cenario.esperado)
        r.comportamentos = metricas.comportamentos(
            estado, mensagens, injecao=bool(cenario.esperado.get("injecao"))
        )
        r.erros += [
            f"comportamento obrigatório falhou: {nome}"
            for nome, ok in r.comportamentos.items()
            if ok is False and nome != "no_maximo_uma_pergunta"
        ]
        if self.juiz is not None:
            r.juiz = asyncio.run(self.juiz.julgar(mensagens))
        self._enviar_scores(r, estado)

    def _enviar_scores(self, r: ResultadoCenario, estado: dict[str, Any]) -> None:
        """Scores no Langfuse (se configurado): por sessão (= lead) e no último rastro."""
        if not os.environ.get("LANGFUSE_PUBLIC_KEY") and not _langfuse_no_env():
            return
        try:
            from langfuse import Langfuse  # noqa: PLC0415 — opcional

            cliente = Langfuse()
            sessao = str(estado.get("id"))
            scores: dict[str, float] = {"eval.cenario_ok": float(r.ok)}
            if r.juiz is not None:
                scores |= {f"eval.juiz.{c}": float(n) for c, n in r.juiz.notas.items()}
                scores["eval.juiz.media"] = r.juiz.media
            if (taxa := r.intencao.taxa) is not None:
                scores["eval.intencao"] = taxa
            if (cobertura := r.extracao.cobertura) is not None:
                scores["eval.extracao.cobertura"] = cobertura
            ultimo = next((t.trace_id for t in reversed(r.turnos) if t.trace_id), None)
            for nome, valor in scores.items():
                cliente.create_score(name=nome, value=valor, session_id=sessao, comment=r.nome)
                if ultimo:
                    cliente.create_score(name=nome, value=valor, trace_id=ultimo, comment=r.nome)
            cliente.flush()
        except Exception:
            logger.warning("não foi possível enviar scores ao Langfuse", exc_info=True)


def _langfuse_no_env() -> bool:
    from sdr.config.settings import obter_settings  # noqa: PLC0415

    s = obter_settings()
    if s.langfuse_public_key and s.langfuse_secret_key:
        os.environ.setdefault("LANGFUSE_PUBLIC_KEY", s.langfuse_public_key)
        os.environ.setdefault("LANGFUSE_SECRET_KEY", s.langfuse_secret_key.get_secret_value())
        os.environ.setdefault("LANGFUSE_HOST", s.langfuse_host)
        return True
    return False


def _ultima_fala(historico: dict[str, Any]) -> str:
    return max(m["criada_em"] for m in historico["mensagens"] if m["papel"] == "lead")


def _commit() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "?"


def main() -> None:
    from evals.relatorio import escrever_relatorio  # noqa: PLC0415

    parser = argparse.ArgumentParser(prog="python -m evals.runner")
    parser.add_argument("--api", help="URL da API (padrão: API_URL ou localhost:8000)")
    parser.add_argument("-k", "--filtro", help="só cenários cujo nome contém o texto")
    parser.add_argument("--sem-juiz", action="store_true", help="não chama o LLM-as-judge")
    parser.add_argument("--saida", default=str(PASTA / "relatorio.md"))
    parser.add_argument("--json", default=str(PASTA / "resultados.json"))
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s %(message)s")

    api = ApiViva(args.api)
    if not api.no_ar():
        raise SystemExit(f"API fora do ar em {api.url}")
    producao = api._http.get("/docs").status_code == 404  # noqa: PLR2004
    juiz = None if args.sem_juiz else Juiz()
    runner = Runner(api, juiz=juiz, producao=producao)
    cenarios = [c for c in carregar_cenarios() if not args.filtro or args.filtro in c.nome]
    resultados = []
    inicio = datetime.now(UTC)
    for cenario in cenarios:
        print(f"▶ {cenario.nome} …", flush=True)
        resultado = runner.executar(cenario)
        situacao = "pulado" if resultado.pulado else "ok" if resultado.ok else "FALHOU"
        print(f"  {situacao} ({resultado.duracao_s:.0f}s)", flush=True)
        for erro in resultado.erros:
            print(f"    - {erro}", flush=True)
        resultados.append(resultado)
    api.fechar()
    contexto = {
        "inicio": inicio.isoformat(timespec="seconds"),
        "api": api.url,
        "producao": producao,
        "commit": _commit(),
        "juiz": juiz.modelo if juiz else None,
        "rubrica": VERSAO_RUBRICA,
        "debounce_s": float(os.environ.get("DEBOUNCE_SEGUNDOS", "5")),
    }
    Path(args.json).write_text(
        json.dumps(
            {"contexto": contexto, "cenarios": [asdict(r) for r in resultados]},
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    escrever_relatorio(Path(args.saida), resultados, contexto)
    print(f"\nRelatório: {args.saida}")


if __name__ == "__main__":
    main()
