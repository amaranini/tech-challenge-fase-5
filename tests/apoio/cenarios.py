"""Roteiros de conversa (evals/cenarios/*.json) e verificação do estado final do lead.

Formato de um roteiro:
    {
      "nome": "...", "descricao": "...",
      "falas": ["fala 1 do lead", "fala 2", ...],      # cada uma espera a resposta da Lia
      # uma fala também pode conferir o estado logo depois dela:
      #   {"texto": "pode ser quinta?", "esperado": {"eventos_ausentes": ["AgendamentoCriado"]}}
      # ... e a resposta: {"sem_resposta": true} (IA em silêncio) ou
      #   {"resposta_contem": ["850"]} (todas as substrings, sem caixa) e/ou
      #   {"resposta_nao_contem": ["tenho sim"]} (nenhuma delas)
      # passos da equipe (tela Fila), via API:
      #   {"acao": "assumir", "responsavel": "Rafael"} | {"acao": "responder", "texto": "..."}
      #   | {"acao": "devolver"}
      "esperado": {
        "intencao": "compra",                           # igualdade
        "atendimento_estado": "aguardando_humano",      # estado de atendimento (IA × humano)
        "proxima_acao": "agendar_visita",
        "classificacao": {"um_de": ["quente"]},
        "score_min": 70,
        "ficha": {"regiao": {"contem": "sul"}, "quartos": 2},   # ficha da intenção atual
        "fichas": {"aluguel": {"aluguel_max": 4000}},           # ficha de outra intenção
        "eventos": [{"tipo": "IntencaoAlterada", "payload": {"de": "aluguel"}}],
        "eventos_ausentes": ["IntencaoAlterada"],
        "contagem_eventos": {"AgendamentoCriado": 1},       # quantas vezes ocorreu
        "resumo": {                                         # resumo de handoff (fora do turno)
          "gatilho": "AgendamentoCriado",
          "secoes": {"agendamento": {"preenchido": true}}   # conteúdo de cada seção
        }
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
    acao: dict[str, Any] | None = None  # passo da equipe (assumir/responder/devolver)


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
    if "acao" in bruta:
        return Fala("", acao=dict(bruta))
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
        return (valor not in (None, "", [], {}, "não informado")) == esperado["preenchido"]
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
    if "atendimento_estado" in esperado:
        obtido = estado.get("atendimento", {}).get("estado")
        if not confere(obtido, esperado["atendimento_estado"]):
            erros.append(
                f"atendimento: esperado {esperado['atendimento_estado']!r}, obtido {obtido!r}"
            )
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


def _falas_do_lead(mensagens: list[dict[str, Any]]) -> str:
    return " \n ".join(
        " ".join(m["texto"].casefold().split()) for m in mensagens if m["papel"] == "lead"
    )


def _literal(trecho: object, falas: str) -> bool:
    alvo = " ".join(str(trecho).casefold().split()).strip(" \"'“”.,;:!?…")
    return bool(alvo) and alvo in falas


def divergencias_resumo(
    resumo: dict[str, Any], esperado: dict[str, Any], mensagens: list[dict[str, Any]]
) -> list[str]:
    """Confere as seções esperadas e que NADA no resumo foi inventado: imóveis só os
    citados na conversa; trechos e evidências, falas literais do lead."""
    secoes = {s["chave"]: s for s in resumo["secoes"]}
    erros = [
        f"resumo.{chave}: esperado {exp!r}, obtido {secoes.get(chave, {}).get('conteudo')!r}"
        for chave, exp in esperado.get("secoes", {}).items()
        if not confere(secoes.get(chave, {}).get("conteudo"), exp)
    ]
    falas = _falas_do_lead(mensagens)
    citados = {
        item["id"] for m in mensagens for item in m.get("itens_citados", []) if item.get("id")
    }
    for secao in resumo["secoes"]:
        conteudo = secao["conteudo"]
        if not isinstance(conteudo, list):
            continue
        for item in conteudo:
            if secao["tipo"] == "itens_catalogo" and item["id"] not in citados:
                erros.append(f"resumo.{secao['chave']}: {item['id']} nunca foi citado")
            if secao["tipo"] == "trechos" and not _literal(item, falas):
                erros.append(f"resumo.{secao['chave']}: trecho inventado {item!r}")
            if secao["tipo"] == "lista" and not _literal(item["evidencia"], falas):
                erros.append(f"resumo.{secao['chave']}: evidência inventada {item!r}")
    return erros
