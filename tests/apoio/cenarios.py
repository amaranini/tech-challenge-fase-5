"""Roteiros de conversa (evals/cenarios/*.json) e verificação do estado final do lead.

Formato de um roteiro:
    {
      "nome": "...", "descricao": "...",
      "falas": ["fala 1 do lead", "fala 2", ...],      # cada uma espera a resposta da Lia
      "esperado": {
        "intencao": "compra",                           # igualdade
        "proxima_acao": "agendar_visita",
        "classificacao": {"um_de": ["quente"]},
        "score_min": 70,
        "ficha": {"regiao": {"contem": "sul"}, "quartos": 2},   # ficha da intenção atual
        "fichas": {"aluguel": {"aluguel_max": 4000}},           # ficha de outra intenção
        "eventos": [{"tipo": "IntencaoAlterada", "payload": {"de": "aluguel"}}],
        "eventos_ausentes": ["IntencaoAlterada"]
      }
    }

Expectativa de valor: escalar = igualdade; ou um dicionário com UMA das chaves
`contem` (substring sem caixa, ou elemento de lista), `contem_todos` (lista),
`um_de` (lista de valores aceitos) ou `preenchido` (true/false).
Os asserts olham o ESTADO (GET /leads/{id}), nunca o texto da Lia — robustos à variação
do LLM.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

RAIZ = Path(__file__).resolve().parents[2]
PASTA_CENARIOS = RAIZ / "evals" / "cenarios"


@dataclass(frozen=True)
class Cenario:
    nome: str
    descricao: str
    falas: list[str]
    esperado: dict[str, Any]
    arquivo: Path


def carregar_cenarios(pasta: Path = PASTA_CENARIOS) -> list[Cenario]:
    cenarios = []
    for arquivo in sorted(pasta.glob("*.json")):
        dados = json.loads(arquivo.read_text(encoding="utf-8"))
        cenarios.append(
            Cenario(
                nome=dados["nome"],
                descricao=dados.get("descricao", ""),
                falas=list(dados["falas"]),
                esperado=dict(dados["esperado"]),
                arquivo=arquivo,
            )
        )
    return cenarios


def _normalizar(valor: object) -> object:
    return valor.casefold() if isinstance(valor, str) else valor


def confere(valor: object, esperado: object) -> bool:
    if not isinstance(esperado, dict):
        return valor == esperado
    if "preenchido" in esperado:
        return (valor not in (None, "", [], {})) == esperado["preenchido"]
    if "um_de" in esperado:
        return valor in esperado["um_de"]
    if "contem" in esperado:
        alvo = _normalizar(esperado["contem"])
        if isinstance(valor, str) and isinstance(alvo, str):
            return alvo in valor.casefold()
        return isinstance(valor, list) and alvo in [_normalizar(v) for v in valor]
    if "contem_todos" in esperado:
        return isinstance(valor, list) and all(v in valor for v in esperado["contem_todos"])
    raise ValueError(f"expectativa desconhecida: {esperado}")


def _conferir_ficha(nome: str, ficha: dict[str, Any], esperada: dict[str, Any]) -> list[str]:
    return [
        f"{nome}.{campo}: esperado {exp!r}, obtido {ficha.get(campo)!r}"
        for campo, exp in esperada.items()
        if not confere(ficha.get(campo), exp)
    ]


def _evento_presente(eventos: list[dict[str, Any]], esperado: dict[str, Any]) -> bool:
    payload = esperado.get("payload", {})
    return any(
        e["tipo"] == esperado["tipo"]
        and all(confere(e["payload"].get(k), v) for k, v in payload.items())
        for e in eventos
    )


def divergencias(estado: dict[str, Any], esperado: dict[str, Any]) -> list[str]:
    """Lista legível de tudo que não bate (vazia = cenário passou)."""
    erros: list[str] = []
    for chave in ("intencao", "proxima_acao", "classificacao"):
        if chave in esperado and not confere(estado.get(chave), esperado[chave]):
            erros.append(f"{chave}: esperado {esperado[chave]!r}, obtido {estado.get(chave)!r}")
    if "score_min" in esperado and (estado.get("score") or 0) < esperado["score_min"]:
        erros.append(f"score: esperado >= {esperado['score_min']}, obtido {estado.get('score')}")
    erros += _conferir_ficha("ficha", estado.get("ficha", {}), esperado.get("ficha", {}))
    for intencao, ficha in esperado.get("fichas", {}).items():
        obtida = estado.get("fichas", {}).get(intencao, {})
        erros += _conferir_ficha(f"fichas.{intencao}", obtida, ficha)
    eventos = estado.get("eventos", [])
    erros += [
        f"evento ausente: {exp}"
        for exp in esperado.get("eventos", [])
        if not _evento_presente(eventos, exp)
    ]
    erros += [
        f"evento inesperado: {tipo}"
        for tipo in esperado.get("eventos_ausentes", [])
        if any(e["tipo"] == tipo for e in eventos)
    ]
    return erros
