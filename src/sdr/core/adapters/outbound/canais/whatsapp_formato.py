"""Conversões puras para o WhatsApp: marcação, divisão de mensagens longas e variáveis de
template nomeadas → numeradas (Twilio Content API: {"1": ..., "2": ...})."""

import re
from collections.abc import Mapping, Sequence

LIMITE_TWILIO_WHATSAPP = 1600  # caracteres por mensagem (Body) no Twilio
# (onde quebrar, como juntar): parágrafo, linha, fim de frase, palavra.
SEPARADORES = ((r"\n\s*\n", "\n\n"), (r"\n", "\n"), (r"(?<=[.!?])\s+", " "), (r"\s+", " "))


def formatar_para_whatsapp(texto: str) -> str:
    """Markdown comum → marcação do WhatsApp (*negrito*, _itálico_, ~tachado~).

    Os prompts pedem texto sem markdown; isto é a rede de segurança."""
    linhas = []
    for original in texto.splitlines():
        linha = original
        if titulo := re.match(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$", linha):
            linha = f"**{titulo.group(1)}**"
        linha = re.sub(r"^(\s*)[*+]\s+", r"\1• ", linha)  # bullet "* item" não vira negrito
        linhas.append(linha)
    texto = "\n".join(linhas)
    texto = re.sub(r"\*\*(.+?)\*\*", r"*\1*", texto)
    texto = re.sub(r"__(.+?)__", r"_\1_", texto)
    texto = re.sub(r"~~(.+?)~~", r"~\1~", texto)
    texto = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r"\1: \2", texto)
    return re.sub(r"\n{3,}", "\n\n", texto).strip()


def dividir_mensagem(texto: str, limite: int = LIMITE_TWILIO_WHATSAPP) -> list[str]:
    """Partes de até `limite` caracteres, quebrando de preferência entre parágrafos, depois
    linhas, frases e palavras (corte seco só se não houver alternativa)."""
    texto = texto.strip()
    if len(texto) <= limite:
        return [texto] if texto else []
    return [p for p in _dividir(texto, limite, SEPARADORES) if p]


def _dividir(texto: str, limite: int, separadores: Sequence[tuple[str, str]]) -> list[str]:
    if len(texto) <= limite:
        return [texto.strip()]
    if not separadores:
        return [texto[i : i + limite].strip() for i in range(0, len(texto), limite)]
    (padrao, juncao), *resto = separadores
    partes: list[str] = []
    atual = ""
    for pedaco in re.split(padrao, texto):
        candidato = f"{atual}{juncao}{pedaco}" if atual else pedaco
        if len(candidato) <= limite:
            atual = candidato
            continue
        if atual:
            partes.append(atual.strip())
        if len(pedaco) > limite:
            *inteiras, atual = _dividir(pedaco, limite, resto)
            partes.extend(inteiras)
        else:
            atual = pedaco
    if atual.strip():
        partes.append(atual.strip())
    return partes


def variaveis_numeradas(
    variaveis: Mapping[str, str], ordem: Sequence[str] | None = None
) -> dict[str, str]:
    """{"primeiro_nome": "Ana", "quando": "qui 14h"} → {"1": "Ana", "2": "qui 14h"}.

    `ordem`: a numeração aprovada no provedor (config); sem ela, a ordem do template lógico
    (a de `variaveis`). KeyError se a ordem pede uma variável que não veio."""
    nomes = list(ordem) if ordem else list(variaveis)
    return {str(i): variaveis[nome] for i, nome in enumerate(nomes, start=1)}
