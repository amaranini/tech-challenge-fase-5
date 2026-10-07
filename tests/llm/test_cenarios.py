"""Cenários de qualificação com o LLM real, roteirizados em evals/cenarios/*.json.

Opt-in: `make llm` (custa tokens; precisa da API no ar com OPENAI_API_KEY). Cada fala
espera a resposta da Lia (debounce + turno) antes da próxima. Os asserts olham o estado
final do lead (GET /leads/{id}) e o painel do Streamlit — nunca o texto exato da Lia.
"""

import json
import uuid
from pathlib import Path
from typing import Any

import pytest
from streamlit.testing.v1 import AppTest

from tests.apoio.api_viva import enviar, exigir_api, obter_lead
from tests.apoio.cenarios import Cenario, carregar_cenarios, divergencias

pytestmark = pytest.mark.llm

APP_WEB = Path(__file__).resolve().parents[2] / "web" / "app.py"
CENARIOS = carregar_cenarios()


def _resumo(estado: dict[str, Any]) -> str:
    return json.dumps(
        {
            k: estado[k]
            for k in (
                "intencao",
                "score",
                "classificacao",
                "proxima_acao",
                "ficha",
                "fichas",
                "campos_faltantes",
                "score_motivos",
            )
        }
        | {"eventos": [e["tipo"] for e in estado["eventos"]]},
        ensure_ascii=False,
        indent=2,
    )


def _conferir_painel(lead_id: str, estado: dict[str, Any]) -> None:
    """O painel ao lado do chat mostra intenção, score, ficha e próxima ação do lead."""
    app = AppTest.from_file(str(APP_WEB), default_timeout=30)
    app.session_state["lead_id"] = lead_id
    app.run()
    assert not app.exception, [e.value for e in app.exception]
    painel = app.columns[1]
    textos = "\n".join(m.value for m in painel.markdown)
    assert f"{estado['score']}/100" in textos
    assert str(estado["classificacao"]) in textos
    assert f"Ficha de {estado['intencao']}" in textos
    if estado["proxima_acao"]:
        assert painel.success, "o painel deveria destacar a próxima ação"


@pytest.mark.parametrize("cenario", CENARIOS, ids=[c.nome for c in CENARIOS])
def test_cenario(cenario: Cenario) -> None:
    exigir_api()
    lead_id = f"llm-{cenario.nome[:20]}-{uuid.uuid4().hex[:6]}"
    print(f"\n=== {cenario.nome} ({lead_id}) — {cenario.descricao}")

    for fala in cenario.falas:
        enviar(lead_id, fala)

    estado = obter_lead(lead_id)
    print(f"--- estado final\n{_resumo(estado)}")
    erros = divergencias(estado, cenario.esperado)
    assert not erros, f"{cenario.arquivo.name}:\n- " + "\n- ".join(erros)
    _conferir_painel(lead_id, estado)
