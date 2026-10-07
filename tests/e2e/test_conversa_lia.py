"""Aceite da Etapa C contra a API no ar (docker compose) e o LLM real.

Opt-in: `make e2e` (custa tokens). Pula se a API não responder ou o LLM estiver sem chave.
Verifica: contexto multi-turno, retomada pelo mesmo lead_id e que todo imóvel citado
existe na base.
"""

import os
import re
import uuid

import httpx
import pytest

pytestmark = pytest.mark.e2e

API_URL = os.environ.get("API_URL", "http://localhost:8000")
CODIGO = re.compile(r"\bIMV-\d{3}\b")


@pytest.fixture(scope="module")
def lead_id() -> str:
    try:
        httpx.get(f"{API_URL}/health", timeout=5).raise_for_status()
    except httpx.HTTPError as erro:
        pytest.skip(f"API indisponível em {API_URL}: {erro}")
    return f"e2e-{uuid.uuid4().hex[:8]}"


def enviar(lead_id: str, texto: str) -> dict[str, object]:
    with httpx.Client(base_url=API_URL, timeout=120) as api:  # cliente novo = "reabrir"
        resposta = api.post("/conversas/mensagens", json={"lead_id": lead_id, "texto": texto})
    if resposta.status_code == 503:
        pytest.skip(f"LLM indisponível: {resposta.json()['detail']}")
    assert resposta.status_code == 200, resposta.text
    corpo: dict[str, object] = resposta.json()
    print(f"\n[lead] {texto}\n[lia]  {corpo['resposta']['texto']}")  # type: ignore[index]
    return corpo


def test_conversa_multiturno_retomada_e_imoveis_reais(lead_id: str) -> None:
    primeira = enviar(
        lead_id, "Oi! Quero comprar um apê de 2 quartos na zona sul, até 800 mil, perto do metrô."
    )
    assert primeira["itens_sugeridos"], "a Lia deveria ter sugerido imóveis da base"

    # Memória dentro da conversa: o orçamento foi dito só no 1º turno.
    memoria = enviar(
        lead_id, "Antes de continuar: qual foi mesmo o orçamento máximo que eu te passei?"
    )
    texto_memoria = memoria["resposta"]["texto"]  # type: ignore[index]
    assert re.search(r"800(\.000| mil|k)", texto_memoria), texto_memoria

    # "Reabrir": outro cliente HTTP, mesmo lead_id → mesma conversa, histórico preservado.
    with httpx.Client(base_url=API_URL, timeout=30) as api:
        historico = api.get("/conversas/mensagens", params={"lead_id": lead_id}).json()
    assert historico["conversa_id"] == primeira["conversa_id"]
    assert len(historico["mensagens"]) == 4

    retomada = enviar(lead_id, "Voltei! Quantos quartos eu tinha pedido mesmo?")
    assert retomada["conversa_id"] == primeira["conversa_id"]
    assert re.search(r"\b(2|dois)\b", retomada["resposta"]["texto"]), retomada  # type: ignore[index]

    # Nada fora da base: todo código citado pela Lia existe.
    with httpx.Client(base_url=API_URL, timeout=30) as api:
        historico = api.get("/conversas/mensagens", params={"lead_id": lead_id}).json()
        citados = {
            codigo
            for m in historico["mensagens"]
            if m["papel"] == "agente"
            for codigo in CODIGO.findall(m["texto"])
        }
        assert citados, "esperava ao menos um código IMV citado"
        for codigo in citados:
            assert api.get(f"/imoveis/{codigo}").status_code == 200, f"{codigo} não existe"
