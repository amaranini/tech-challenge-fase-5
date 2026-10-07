"""Cliente da API no ar (docker compose) para testes com LLM real (e2e e cenários `llm`).

O POST só registra a mensagem (202); a resposta chega depois do debounce, por isso cada
fala espera o turno terminar (polling) antes da próxima.
"""

import os
import time
from typing import Any

import httpx
import pytest

API_URL = os.environ.get("API_URL", "http://localhost:8000")


def exigir_api() -> None:
    """Pula o teste se a API não estiver no ar."""
    try:
        httpx.get(f"{API_URL}/health", timeout=5).raise_for_status()
    except httpx.HTTPError as erro:
        pytest.skip(f"API indisponível em {API_URL}: {erro}")


def postar(lead_id: str, *textos: str) -> None:
    with httpx.Client(base_url=API_URL, timeout=30) as api:
        for texto in textos:
            resposta = api.post("/conversas/mensagens", json={"lead_id": lead_id, "texto": texto})
            assert resposta.status_code == 202, resposta.text
            print(f"\n[lead] {texto}")


def aguardar_resposta(lead_id: str, timeout: float = 120) -> dict[str, Any]:
    """Polling até não haver turno pendente; devolve o histórico."""
    limite = time.monotonic() + timeout
    with httpx.Client(base_url=API_URL, timeout=30) as api:
        while time.monotonic() < limite:
            historico: dict[str, Any] = api.get(f"/conversas/{lead_id}/mensagens").json()
            if not historico["processando"]:
                ultima = historico["mensagens"][-1]
                if ultima["status"] == "falha":
                    pytest.skip("turno falhou (LLM indisponível?) — veja os logs da API")
                return historico
            time.sleep(1)
    raise AssertionError(f"sem resposta em {timeout}s")


def enviar(lead_id: str, texto: str) -> dict[str, Any]:
    """Uma fala do lead → espera e devolve a resposta da Lia (e o id da conversa)."""
    postar(lead_id, texto)
    historico = aguardar_resposta(lead_id)
    resposta = historico["mensagens"][-1]
    assert resposta["papel"] == "agente"
    print(f"[lia]  {resposta['texto']}")
    return {"conversa_id": historico["conversa_id"], "resposta": resposta}


def obter_lead(lead_id: str) -> dict[str, Any]:
    resposta = httpx.get(f"{API_URL}/leads/{lead_id}", timeout=30)
    resposta.raise_for_status()
    corpo: dict[str, Any] = resposta.json()
    return corpo
