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

from tests.apoio.api_viva import (
    acao_da_equipe,
    aguardar_resposta,
    aguardar_resumo,
    enviar,
    exigir_api,
    historico,
    obter_lead,
    postar,
)
from tests.apoio.banco_vivo import registro_crm
from tests.apoio.cenarios import Cenario, carregar_cenarios, divergencias, divergencias_resumo

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
    if estado.get("agendamento"):
        assert painel.info, "o painel deveria mostrar o agendamento"


def _conferir_resumo_na_fila(lead_id: str) -> None:
    """Quem atende vê o resumo na tela Fila, junto da conversa do lead."""
    aguardar_resumo(lead_id, None)  # gerado fora do turno
    app = AppTest.from_file(str(APP_WEB), default_timeout=30)
    app.session_state["tela"] = "fila"
    app.session_state["responsavel_nome"] = "Teste"
    app.run()
    assert not app.exception, [e.value for e in app.exception]
    [bloco] = [e for e in app.expander if e.label.startswith(f"{lead_id} — com")]
    textos = "\n".join(m.value for m in bloco.markdown)
    assert "Resumo do lead para o corretor" in textos, "o resumo não aparece na Fila"
    assert "Necessidades (ficha)" in textos
    print("[fila]   resumo visível para quem atende")


@pytest.mark.parametrize("cenario", CENARIOS, ids=[c.nome for c in CENARIOS])
def test_cenario(cenario: Cenario) -> None:
    exigir_api()
    lead_id = f"llm-{cenario.nome[:20]}-{uuid.uuid4().hex[:6]}"
    print(f"\n=== {cenario.nome} ({lead_id}) — {cenario.descricao}")

    for numero, fala in enumerate(cenario.falas, start=1):
        if fala.acao is not None:
            if fala.acao["acao"] == "conferir_resumo_na_fila":
                _conferir_resumo_na_fila(lead_id)
            else:
                acao_da_equipe(lead_id, fala.acao)
            continue
        esperado = fala.esperado or {}
        if esperado.get("sem_resposta"):
            postar(lead_id, fala.texto)
            ultima = aguardar_resposta(lead_id)["mensagens"][-1]
            assert ultima["papel"] == "lead", f"fala {numero}: a IA respondeu {ultima['texto']!r}"
            print("[lia]  (em silêncio: atendimento humano)")
        else:
            resposta = enviar(lead_id, fala.texto)["resposta"]["texto"]
            faltando = [
                trecho
                for trecho in esperado.get("resposta_contem", [])
                if trecho.casefold() not in resposta.casefold()
            ]
            assert not faltando, f"fala {numero}: resposta sem {faltando}: {resposta!r}"
            proibidos = [
                trecho
                for trecho in esperado.get("resposta_nao_contem", [])
                if trecho.casefold() in resposta.casefold()
            ]
            assert not proibidos, f"fala {numero}: resposta com {proibidos}: {resposta!r}"
        if fala.esperado:
            erros = divergencias(obter_lead(lead_id), fala.esperado)
            assert not erros, f"{cenario.arquivo.name}, fala {numero} ({fala.texto!r}):\n- " + (
                "\n- ".join(erros)
            )

    estado = obter_lead(lead_id)
    print(f"--- estado final\n{_resumo(estado)}")
    erros = divergencias(estado, cenario.esperado)
    assert not erros, f"{cenario.arquivo.name}:\n- " + "\n- ".join(erros)
    if esperado_resumo := cenario.esperado.get("resumo"):
        _conferir_resumo(lead_id, esperado_resumo, cenario)
    _conferir_painel(lead_id, estado)


def _conferir_resumo(lead_id: str, esperado: dict[str, Any], cenario: Cenario) -> None:
    """Resumo gerado fora do turno: seções esperadas, nada inventado e cópia no CRM mock."""
    resumo = aguardar_resumo(lead_id, esperado.get("gatilho"))
    print(f"--- resumo v{resumo['versao']} ({resumo['gatilho']})")
    for secao in resumo["secoes"]:
        print(f"  {secao['titulo']}: {json.dumps(secao['conteudo'], ensure_ascii=False)}")
    if resumo["descartados"]:
        print(f"  [ancoragem descartou] {resumo['descartados']}")
    erros = divergencias_resumo(resumo, esperado, historico(lead_id)["mensagens"])
    assert not erros, f"{cenario.arquivo.name} (resumo):\n- " + "\n- ".join(erros)
    crm = registro_crm(lead_id)
    assert crm is not None, "lead não chegou ao CRM mock"
    assert crm["resumo_versao"] == resumo["versao"]
