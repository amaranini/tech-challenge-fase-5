"""Aceite da Etapa C contra a API no ar (docker compose) e o LLM real.

Opt-in: `make e2e` (custa tokens). Pula se a API não responder ou o LLM estiver sem chave.
Verifica: contexto multi-turno, retomada pelo mesmo lead_id e que todo imóvel citado
existe na base.
"""

import os
import re
import time
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


def postar(lead_id: str, *textos: str) -> None:
    with httpx.Client(base_url=API_URL, timeout=30) as api:
        for texto in textos:
            resposta = api.post("/conversas/mensagens", json={"lead_id": lead_id, "texto": texto})
            assert resposta.status_code == 202, resposta.text
            print(f"\n[lead] {texto}")


def aguardar_resposta(lead_id: str, timeout: float = 120) -> dict[str, object]:
    """Polling até não haver turno pendente; devolve o histórico."""
    limite = time.monotonic() + timeout
    with httpx.Client(base_url=API_URL, timeout=30) as api:
        while time.monotonic() < limite:
            historico: dict[str, object] = api.get(f"/conversas/{lead_id}/mensagens").json()
            if not historico["processando"]:
                ultima = historico["mensagens"][-1]  # type: ignore[index]
                if ultima["status"] == "falha":
                    pytest.skip("turno falhou (LLM indisponível?) — veja os logs da API")
                return historico
            time.sleep(1)
    raise AssertionError(f"sem resposta em {timeout}s")


def enviar(lead_id: str, texto: str) -> dict[str, object]:
    postar(lead_id, texto)
    historico = aguardar_resposta(lead_id)
    resposta = historico["mensagens"][-1]  # type: ignore[index]
    assert resposta["papel"] == "agente"
    print(f"[lia]  {resposta['texto']}")
    return {"conversa_id": historico["conversa_id"], "resposta": resposta}


def test_conversa_multiturno_retomada_e_imoveis_reais(lead_id: str) -> None:
    primeira = enviar(
        lead_id, "Oi! Quero comprar um apê de 2 quartos na zona sul, até 800 mil, perto do metrô."
    )
    assert primeira["resposta"]["itens_citados"], "a Lia deveria ter sugerido imóveis da base"  # type: ignore[index]

    # Memória dentro da conversa: o orçamento foi dito só no 1º turno.
    memoria = enviar(
        lead_id, "Antes de continuar: qual foi mesmo o orçamento máximo que eu te passei?"
    )
    texto_memoria = memoria["resposta"]["texto"]  # type: ignore[index]
    assert re.search(r"800(\.000| mil|k)", texto_memoria), texto_memoria

    # "Reabrir": outro cliente HTTP, mesmo lead_id → mesma conversa, histórico preservado.
    with httpx.Client(base_url=API_URL, timeout=30) as api:
        historico = api.get(f"/conversas/{lead_id}/mensagens").json()
    assert historico["conversa_id"] == primeira["conversa_id"]
    assert len(historico["mensagens"]) == 4

    retomada = enviar(lead_id, "Voltei! Quantos quartos eu tinha pedido mesmo?")
    assert retomada["conversa_id"] == primeira["conversa_id"]
    assert re.search(r"\b(2|dois)\b", retomada["resposta"]["texto"]), retomada  # type: ignore[index]

    # Nada fora da base: todo código citado pela Lia existe.
    with httpx.Client(base_url=API_URL, timeout=30) as api:
        historico = api.get(f"/conversas/{lead_id}/mensagens").json()
        citados = {
            codigo
            for m in historico["mensagens"]
            if m["papel"] == "agente"
            for codigo in CODIGO.findall(m["texto"])
        }
        assert citados, "esperava ao menos um código IMV citado"
        for codigo in citados:
            assert api.get(f"/imoveis/{codigo}").status_code == 200, f"{codigo} não existe"


def test_mensagens_em_sequencia_rapida_recebem_uma_unica_resposta() -> None:
    """Aceite da Etapa 0 (debounce): 3 mensagens seguidas → 1 resposta ao conjunto."""
    lead = f"e2e-deb-{uuid.uuid4().hex[:6]}"
    with httpx.Client(base_url=API_URL, timeout=5) as api:
        try:
            api.get("/health").raise_for_status()
        except httpx.HTTPError as erro:
            pytest.skip(f"API indisponível em {API_URL}: {erro}")

    postar(lead, "procuro apê", "zona sul", "até 800 mil")
    historico = aguardar_resposta(lead)

    mensagens = historico["mensagens"]  # type: ignore[assignment]
    papeis = [m["papel"] for m in mensagens]  # type: ignore[attr-defined]
    assert papeis == ["lead", "lead", "lead", "agente"], papeis
    resposta = mensagens[-1]["texto"]  # type: ignore[index]
    print(f"[lia]  {resposta}")
    assert re.search(r"(?i)sul", resposta), "a resposta deveria considerar a zona sul"
    assert re.search(r"800", resposta) or mensagens[-1]["itens_citados"], (  # type: ignore[index]
        "a resposta deveria considerar o orçamento (citando-o ou sugerindo imóveis)"
    )
