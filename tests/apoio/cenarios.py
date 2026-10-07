"""Roteiros de conversa (evals/cenarios/*.json) e verificação do estado final do lead.

Formato de um roteiro:
    {
      "nome": "...", "descricao": "...",
      "falas": ["fala 1 do lead", "fala 2", ...],      # cada uma espera a resposta da Lia
      # uma fala também pode conferir o estado logo depois dela:
      #   {"texto": "pode ser quinta?", "esperado": {"eventos_ausentes": ["AgendamentoCriado"]}}
      "esperado": {
        "intencao": "compra",                           # igualdade
        "proxima_acao": "agendar_visita",
        "classificacao": {"um_de": ["quente"]},
        "score_min": 70,
        "ficha": {"regiao": {"contem": "sul"}, "quartos": 2},   # ficha da intenção atual
        "fichas": {"aluguel": {"aluguel_max": 4000}},           # ficha de outra intenção
        "eventos": [{"tipo": "IntencaoAlterada", "payload": {"de": "aluguel"}}],
        "eventos_ausentes": ["IntencaoAlterada"],
        "contagem_eventos": {"AgendamentoCriado": 1}        # quantas vezes ocorreu
      }
    }

Expectativa de valor: escalar = igualdade; ou um dicionário com UMA das chaves
`contem` (substring sem caixa, ou elemento de lista), `contem_todos` (lista),
`um_de` (lista de valores aceitos) ou `preenchido` (true/false); para horários ISO 8601,
`dia_semana` ("terca") e/ou `periodo` ("manha" | "tarde" | "noite"), em America/Sao_Paulo.
Os asserts olham o ESTADO (GET /leads/{id}), nunca o texto da Lia — robustos à variação
do LLM.
"""

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

FUSO = ZoneInfo("America/Sao_Paulo")
DIAS = ("segunda", "terca", "quarta", "quinta", "sexta", "sabado", "domingo")
MEIO_DIA, SEIS_DA_TARDE = 12, 18

RAIZ = Path(__file__).resolve().parents[2]
PASTA_CENARIOS = RAIZ / "evals" / "cenarios"


@dataclass(frozen=True)
class Fala:
    texto: str
    esperado: dict[str, Any] | None = None  # conferido logo após a resposta a esta fala


@dataclass(frozen=True)
class Cenario:
    nome: str
    descricao: str
    falas: list[Fala]
    esperado: dict[str, Any]
    arquivo: Path


def _fala(bruta: str | dict[str, Any]) -> Fala:
    if isinstance(bruta, str):
        return Fala(bruta)
    return Fala(bruta["texto"], bruta.get("esperado"))


def carregar_cenarios(pasta: Path = PASTA_CENARIOS) -> list[Cenario]:
    cenarios = []
    for arquivo in sorted(pasta.glob("*.json")):
        dados = json.loads(arquivo.read_text(encoding="utf-8"))
        cenarios.append(
            Cenario(
                nome=dados["nome"],
                descricao=dados.get("descricao", ""),
                falas=[_fala(f) for f in dados["falas"]],
                esperado=dict(dados["esperado"]),
                arquivo=arquivo,
            )
        )
    return cenarios


def _normalizar(valor: object) -> object:
    return valor.casefold() if isinstance(valor, str) else valor


def _confere_horario(valor: object, esperado: dict[str, Any]) -> bool:
    try:
        local = datetime.fromisoformat(str(valor)).astimezone(FUSO)
    except ValueError:
        return False
    hora = local.hour
    periodo = "manha" if hora < MEIO_DIA else "tarde" if hora < SEIS_DA_TARDE else "noite"
    return esperado.get("dia_semana", DIAS[local.weekday()]) == DIAS[local.weekday()] and (
        esperado.get("periodo", periodo) == periodo
    )


def _contem(valor: object, alvo: object) -> bool:
    if isinstance(valor, str) and isinstance(alvo, str):
        return alvo in valor.casefold()
    return isinstance(valor, list) and alvo in [_normalizar(v) for v in valor]


def confere(valor: object, esperado: object) -> bool:
    if not isinstance(esperado, dict):
        return valor == esperado
    if "dia_semana" in esperado or "periodo" in esperado:
        return _confere_horario(valor, esperado)
    if "preenchido" in esperado:
        return (valor not in (None, "", [], {})) == esperado["preenchido"]
    if "um_de" in esperado:
        return valor in esperado["um_de"]
    if "contem" in esperado:
        return _contem(valor, _normalizar(esperado["contem"]))
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
    eventos = estado.get("eventos", [])
    for tipo, quantos in esperado.get("contagem_eventos", {}).items():
        obtidos = sum(1 for e in eventos if e["tipo"] == tipo)
        if obtidos != quantos:
            erros.append(f"evento {tipo}: esperado {quantos}x, obtido {obtidos}x")
    for chave in ("intencao", "proxima_acao", "classificacao"):
        if chave in esperado and not confere(estado.get(chave), esperado[chave]):
            erros.append(f"{chave}: esperado {esperado[chave]!r}, obtido {estado.get(chave)!r}")
    if "score_min" in esperado and (estado.get("score") or 0) < esperado["score_min"]:
        erros.append(f"score: esperado >= {esperado['score_min']}, obtido {estado.get('score')}")
    erros += _conferir_ficha("ficha", estado.get("ficha", {}), esperado.get("ficha", {}))
    for intencao, ficha in esperado.get("fichas", {}).items():
        obtida = estado.get("fichas", {}).get(intencao, {})
        erros += _conferir_ficha(f"fichas.{intencao}", obtida, ficha)
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
