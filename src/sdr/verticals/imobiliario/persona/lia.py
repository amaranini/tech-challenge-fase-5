"""Persona da vertical imobiliária: Lia. Prompts versionados em ./prompts/<versao>.md."""

from pathlib import Path

from sdr.core.domain.agente import Persona

PASTA_PROMPTS = Path(__file__).resolve().parent / "prompts"
VERSAO_ATUAL = "lia_v2"
PADRAO_CODIGO_IMOVEL = r"\bIMV-\d{3}\b"


def carregar_prompt(caminho_relativo: str) -> str:
    """Prompt versionado em persona/prompts/ (ex.: "especialistas/compra_v1")."""
    caminho = PASTA_PROMPTS / f"{caminho_relativo}.md"
    if not caminho.is_file():
        raise ValueError(f"Prompt {caminho_relativo!r} não encontrado em {PASTA_PROMPTS}")
    return caminho.read_text(encoding="utf-8").strip()


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
        aviso_midia=(
            "Recebi seu anexo, mas por enquanto eu só consigo ler mensagens de texto 😊 "
            "Pode me escrever o que precisa? Assim eu já te ajudo a encontrar o imóvel."
        ),
    )
