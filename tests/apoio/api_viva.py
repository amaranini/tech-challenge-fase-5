"""API no ar para os testes com LLM real (e2e e cenários `llm`): fina camada de pytest
sobre o cliente da avaliação (evals/cliente.py) — pula se a API ou o LLM estiverem fora.

O POST só registra a mensagem (202); a resposta chega depois do debounce, por isso cada
fala espera o turno terminar (polling) antes da próxima.
"""

import os
from typing import Any

import pytest

from evals.cliente import ApiViva, TurnoFalhouError, chave_admin

API = ApiViva()
API_URL = API.url
# O dashboard, quando testado aqui (AppTest), lê API_KEY do ambiente.
if chave := chave_admin():
    os.environ.setdefault("API_KEY", chave)


def exigir_api() -> None:
    """Pula o teste se a API não estiver no ar."""
    if not API.no_ar():
        pytest.skip(f"API indisponível em {API_URL}")


def postar(lead_id: str, *textos: str) -> None:
    API.postar(lead_id, *textos)
    for texto in textos:
        print(f"\n[lead] {texto}")


def aguardar_resposta(lead_id: str, timeout: float = 120) -> dict[str, Any]:
    try:
        return API.aguardar(lead_id, timeout)
    except TurnoFalhouError:
        pytest.skip("turno falhou (LLM indisponível?) — veja os logs da API")


def enviar(lead_id: str, texto: str) -> dict[str, Any]:
    """Uma fala do lead → espera e devolve a resposta da Lia (e o id da conversa)."""
    postar(lead_id, texto)
    historico = aguardar_resposta(lead_id)
    resposta = historico["mensagens"][-1]
    assert resposta["papel"] == "assistente"
    print(f"[lia]  {resposta['texto']}")
    return {"conversa_id": historico["conversa_id"], "resposta": resposta}


def obter_lead(lead_id: str) -> dict[str, Any]:
    return API.lead(lead_id)


def historico(lead_id: str) -> dict[str, Any]:
    return API.historico(lead_id)


def aguardar_resumo(lead_id: str, gatilho: str | None, timeout: float = 120) -> dict[str, Any]:
    return API.aguardar_resumo(lead_id, gatilho, timeout)


def acao_da_equipe(lead_id: str, acao: dict[str, Any]) -> None:
    """Passo da tela Fila (assumir, responder, devolver) ou da demo (inatividade)."""
    followup = API.acao_da_equipe(lead_id, acao)
    print(f"\n[equipe] {acao['acao']} {acao.get('texto', acao.get('responsavel', ''))}")
    if followup:
        print(f"[lia]  (follow-up) {followup}")
