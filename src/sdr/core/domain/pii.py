"""Mascaramento de dados pessoais (telefone, e-mail, CPF) para logs e observabilidade.

Puro: só expressões regulares. Mantém o suficiente para depurar (DDI/DDD e os 4 últimos
dígitos do telefone, o domínio do e-mail) sem expor a pessoa.
"""

import re

# Telefone: +55 11 98765-4321, 5511987654321, whatsapp:+5511987654321, (11) 98765-4321...
_TELEFONE = re.compile(r"(?<![\w.])(\+?\d[\d\s().-]{8,}\d)(?![\w.])")
_EMAIL = re.compile(r"\b([A-Za-z0-9._%+-])[A-Za-z0-9._%+-]*@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b")
_CPF = re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b")
_TELEFONE_URL = re.compile(r"%2B(\d{10,15})(?!\d)", re.IGNORECASE)  # "+55..." codificado
_DATA = re.compile(r"\d{4}-\d{2}-\d{2}")
MIN_DIGITOS, MIN_TELEFONE, MAX_TELEFONE = 8, 10, 15  # E.164: até 15 dígitos


def mascarar_telefone(telefone: str) -> str:
    """+5511987654321 → +55119****4321 (pontuação descartada)."""
    digitos = re.sub(r"\D", "", telefone)
    if len(digitos) < MIN_DIGITOS:
        return telefone
    prefixo = "+" if telefone.lstrip().startswith("+") else ""
    visiveis = min(5, len(digitos) - MIN_DIGITOS)
    return f"{prefixo}{digitos[:visiveis]}{'*' * (len(digitos) - visiveis - 4)}{digitos[-4:]}"


def mascarar_pii(texto: str) -> str:
    """Mascara CPF, e-mail e telefone em texto livre (nessa ordem: CPF antes de telefone)."""
    texto = _CPF.sub("***.***.***-**", texto)
    texto = _EMAIL.sub(lambda m: f"{m.group(1)}***@{m.group(2)}", texto)
    texto = _TELEFONE_URL.sub(lambda m: "%2B" + mascarar_telefone(m.group(1)), texto)
    return _TELEFONE.sub(lambda m: _mascarar_se_telefone(m.group(1)), texto)


def _mascarar_se_telefone(candidato: str) -> str:
    digitos = re.sub(r"\D", "", candidato)
    # 10–15 dígitos (E.164 tem no máximo 15); evita mascarar valores, datas e horários.
    if not MIN_TELEFONE <= len(digitos) <= MAX_TELEFONE or _DATA.match(candidato):
        return candidato
    return mascarar_telefone(candidato)
