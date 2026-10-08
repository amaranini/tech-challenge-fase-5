"""Aceite da Etapa C contra a API no ar (docker compose) e o LLM real.

Opt-in: `make e2e` (custa tokens). Pula se a API não responder ou o LLM estiver sem chave.
Verifica: contexto multi-turno, retomada pelo mesmo lead_id e que todo imóvel citado
existe na base.
"""

import re
import uuid

import httpx
import pytest

from tests.apoio.api_viva import API_URL, aguardar_resposta, enviar, exigir_api, postar

pytestmark = pytest.mark.e2e

CODIGO = re.compile(r"\bIMV-\d{3}\b")


@pytest.fixture(scope="module")
def lead_id() -> str:
    exigir_api()
    return f"e2e-{uuid.uuid4().hex[:8]}"


def test_conversa_multiturno_retomada_e_imoveis_reais(lead_id: str) -> None:
    primeira = enviar(
        lead_id, "Oi! Quero comprar um apê de 2 quartos na zona sul, até 800 mil, perto do metrô."
    )
    assert primeira["resposta"]["itens_citados"], "a Lia deveria ter sugerido imóveis da base"

    # Memória dentro da conversa: o orçamento foi dito só no 1º turno.
    memoria = enviar(
        lead_id, "Antes de continuar: qual foi mesmo o orçamento máximo que eu te passei?"
    )
    texto_memoria = memoria["resposta"]["texto"]
    assert re.search(r"800(\.000| mil|k)", texto_memoria), texto_memoria

    # "Reabrir": outro cliente HTTP, mesmo lead_id → mesma conversa, histórico preservado.
    with httpx.Client(base_url=API_URL, timeout=30) as api:
        historico = api.get(f"/conversas/{lead_id}/mensagens").json()
    assert historico["conversa_id"] == primeira["conversa_id"]
    assert len(historico["mensagens"]) == 4

    retomada = enviar(lead_id, "Voltei! Quantos quartos eu tinha pedido mesmo?")
    assert retomada["conversa_id"] == primeira["conversa_id"]
    assert re.search(r"\b(2|dois)\b", retomada["resposta"]["texto"]), retomada

    # Nada fora da base: todo código citado pela Lia existe.
    with httpx.Client(base_url=API_URL, timeout=30) as api:
        historico = api.get(f"/conversas/{lead_id}/mensagens").json()
        citados = {
            codigo
            for m in historico["mensagens"]
            if m["papel"] == "assistente"
            for codigo in CODIGO.findall(m["texto"])
        }
        assert citados, "esperava ao menos um código IMV citado"
        for codigo in citados:
            assert api.get(f"/imoveis/{codigo}").status_code == 200, f"{codigo} não existe"


def test_mensagens_em_sequencia_rapida_recebem_uma_unica_resposta() -> None:
    """Aceite da Etapa 0 (debounce): 3 mensagens seguidas → 1 resposta ao conjunto."""
    exigir_api()
    lead = f"e2e-deb-{uuid.uuid4().hex[:6]}"

    postar(lead, "procuro apê", "zona sul", "até 800 mil")
    historico = aguardar_resposta(lead)

    mensagens = historico["mensagens"]
    papeis = [m["papel"] for m in mensagens]
    assert papeis == ["lead", "lead", "lead", "assistente"], papeis
    resposta = mensagens[-1]["texto"]
    print(f"[lia]  {resposta}")
    assert re.search(r"(?i)sul", resposta), "a resposta deveria considerar a zona sul"
    assert re.search(r"800", resposta) or mensagens[-1]["itens_citados"], (
        "a resposta deveria considerar o orçamento (citando-o ou sugerindo imóveis)"
    )
