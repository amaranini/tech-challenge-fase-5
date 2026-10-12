"""Persona da vertical imobiliária: Lia. Prompts versionados em ./prompts/<versao>.md."""

from pathlib import Path

from sdr.core.domain.agente import Persona

PASTA_PROMPTS = Path(__file__).resolve().parent / "prompts"
VERSAO_ATUAL = "lia_v2"
PADRAO_CODIGO_IMOVEL = r"\bIMV-\d{3}\b"
# (nome, regex sobre texto minúsculo e sem acento)
PROMESSAS_IMOBILIARIAS: tuple[tuple[str, str], ...] = (
    (
        "aceite_do_proprietario",
        r"\b(proprietario|dono)\b.{0,15}\b(aceita|topa|vai aceitar)\b.{0,15}"
        r"\b(proposta|oferta|contraproposta|esse valor|o valor|\d)",
    ),
    ("financiamento_aprovado", r"\bfinanciamento\b.{0,25}\b(aprovado|garantido|sai com certeza)\b"),
    ("isencao_itbi", r"\b(sem|isento de|nao paga) itbi\b"),
)


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
        # Resposta segura (saída reprovada duas vezes): honesta, sem prometer retorno.
        mensagem_fallback=(
            "Prefiro não te passar nada que eu não tenha certeza 🙂 Valores, condições e "
            "negociação quem confirma é o corretor. Me conta o que mais importa para você "
            "no imóvel que eu busco opções na nossa base?"
        ),
        # Promessas típicas do segmento, além das genéricas do core (ADR 015).
        padroes_promessa=PROMESSAS_IMOBILIARIAS,
        aviso_midia=(
            "Recebi seu anexo, mas por enquanto eu só consigo ler mensagens de texto 😊 "
            "Pode me escrever o que precisa? Assim eu já te ajudo a encontrar o imóvel."
        ),
    )
