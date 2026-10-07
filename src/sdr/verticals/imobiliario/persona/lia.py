"""Persona da vertical imobiliária: Lia. Prompts versionados em ./prompts/<versao>.md."""

from pathlib import Path

from sdr.core.domain.agente import Persona

PASTA_PROMPTS = Path(__file__).resolve().parent / "prompts"
VERSAO_ATUAL = "lia_v1"
PADRAO_CODIGO_IMOVEL = r"\bIMV-\d{3}\b"


def carregar_persona(versao: str = VERSAO_ATUAL) -> Persona:
    caminho = PASTA_PROMPTS / f"{versao}.md"
    if not caminho.is_file():
        disponiveis = sorted(p.stem for p in PASTA_PROMPTS.glob("*.md"))
        raise ValueError(f"Prompt {versao!r} não encontrado; disponíveis: {disponiveis}")
    return Persona(
        nome="Lia",
        versao_prompt=versao,
        prompt_sistema=caminho.read_text(encoding="utf-8").strip(),
        padrao_codigo_item=PADRAO_CODIGO_IMOVEL,
        mensagem_fallback=(
            "Deixa eu conferir as opções certinho no sistema e já te mando, tá bom?"
        ),
    )
