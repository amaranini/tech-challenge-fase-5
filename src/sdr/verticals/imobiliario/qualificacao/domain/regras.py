"""Regras de qualificação imobiliária: campos por intenção, scoring explicável e critério.

Puro (sem Pydantic): opera sobre a ficha já validada (dict JSON). Os nomes de campo aqui
são a fonte da verdade — os schemas Pydantic e a prioridade do slot filling os espelham
(há teste garantindo).

Scoring (0–100), pontos explícitos por intenção:
- orçamento definido ............................ 25
- clareza (região / objetivo do investimento) ... 15
- urgência (prazo) .............................. até 20
- forma de pagamento / garantia / experiência ... 15
- completude da ficha ........................... até 25 (proporcional)
Classificação: quente ≥ 70 · morno ≥ 40 · frio < 40.
"""

from collections.abc import Mapping

from sdr.core.domain.qualificacao import Classificacao, Score

COMPRA = "compra"
ALUGUEL = "aluguel"
INVESTIMENTO = "investimento"

# Ordem = prioridade do slot filling (o especialista pergunta nessa ordem).
CAMPOS: dict[str, tuple[str, ...]] = {
    COMPRA: (
        "regiao",
        "preco_max",
        "quartos",
        "tipo_imovel",
        "urgencia",
        "forma_pagamento",
        "vagas",
    ),
    ALUGUEL: (
        "regiao",
        "aluguel_max",
        "quartos",
        "prazo_mudanca",
        "tipo_garantia",
        "aceita_pets",
    ),
    INVESTIMENTO: (
        "ticket",
        "objetivo",
        "expectativa_retorno",
        "prazo_investimento",
        "experiencia_previa",
    ),
}

# Mínimo para considerar o lead qualificado (passar ao próximo passo comercial).
ESSENCIAIS: dict[str, tuple[str, ...]] = {
    COMPRA: ("regiao", "preco_max", "quartos", "urgencia", "forma_pagamento"),
    ALUGUEL: ("regiao", "aluguel_max", "quartos", "prazo_mudanca", "tipo_garantia"),
    INVESTIMENTO: ("ticket", "objetivo", "prazo_investimento", "experiencia_previa"),
}

QUENTE_A_PARTIR = 70
MORNO_A_PARTIR = 40

PONTOS_URGENCIA = {
    # compra
    "imediata": 20,
    "ate_3_meses": 20,
    "3_a_6_meses": 10,
    "6_a_12_meses": 5,
    "sem_pressa": 0,
    # aluguel
    "ate_1_mes": 20,
    "1_a_3_meses": 10,
    "mais_de_3_meses": 5,
    # investimento (prazo do investimento não é urgência; pontua a decisão de compra)
}


def _preenchido(valor: object) -> bool:
    return valor is not None and valor not in ("", [], {})


def _reais(valor: object) -> str:
    try:
        return "R$ " + f"{int(valor):,}".replace(",", ".")  # type: ignore[call-overload]
    except (TypeError, ValueError):
        return str(valor)


def classificar(pontos: int) -> Classificacao:
    if pontos >= QUENTE_A_PARTIR:
        return Classificacao.QUENTE
    if pontos >= MORNO_A_PARTIR:
        return Classificacao.MORNO
    return Classificacao.FRIO


class _Pontuacao:
    def __init__(self) -> None:
        self.pontos = 0
        self.motivos: list[str] = []

    def somar(self, pontos: int, motivo: str) -> None:
        self.pontos += pontos
        self.motivos.append(f"+{pontos} {motivo}")


def _pontuar_compra(ficha: Mapping[str, object], p: _Pontuacao) -> None:
    preco = ficha.get("preco_max")
    if _preenchido(preco):
        p.somar(25, f"orçamento definido (até {_reais(preco)})")
    else:
        p.somar(0, "orçamento ainda não definido")
    if _preenchido(ficha.get("regiao")):
        p.somar(15, f"região clara ({ficha['regiao']})")
    else:
        p.somar(0, "região ainda não definida")
    urgencia = ficha.get("urgencia")
    if _preenchido(urgencia):
        p.somar(PONTOS_URGENCIA.get(str(urgencia), 0), f"urgência: {urgencia}")
    else:
        p.somar(0, "prazo de compra não informado")
    pagamento = ficha.get("forma_pagamento")
    if _preenchido(pagamento):
        formas = ", ".join(pagamento) if isinstance(pagamento, list) else str(pagamento)
        p.somar(15, f"forma de pagamento definida ({formas})")
    else:
        p.somar(0, "forma de pagamento não informada")


def _pontuar_aluguel(ficha: Mapping[str, object], p: _Pontuacao) -> None:
    valor = ficha.get("aluguel_max")
    if _preenchido(valor):
        p.somar(25, f"orçamento definido (até {_reais(valor)}/mês com condomínio)")
    else:
        p.somar(0, "orçamento mensal ainda não definido")
    if _preenchido(ficha.get("regiao")):
        p.somar(15, f"região clara ({ficha['regiao']})")
    else:
        p.somar(0, "região ainda não definida")
    prazo = ficha.get("prazo_mudanca")
    if _preenchido(prazo):
        p.somar(PONTOS_URGENCIA.get(str(prazo), 0), f"urgência para mudar: {prazo}")
    else:
        p.somar(0, "prazo de mudança não informado")
    garantia = ficha.get("tipo_garantia")
    if _preenchido(garantia) and garantia != "nao_sabe":
        p.somar(15, f"garantia locatícia definida ({garantia})")
    else:
        p.somar(0, "garantia locatícia não definida")


def _pontuar_investimento(ficha: Mapping[str, object], p: _Pontuacao) -> None:
    ticket = ficha.get("ticket")
    if _preenchido(ticket):
        p.somar(25, f"ticket definido (até {_reais(ticket)})")
    else:
        p.somar(0, "ticket de investimento não definido")
    if _preenchido(ficha.get("objetivo")):
        p.somar(15, f"objetivo claro ({ficha['objetivo']})")
    else:
        p.somar(0, "objetivo (renda ou valorização) não definido")
    if _preenchido(ficha.get("prazo_investimento")):
        p.somar(10, f"horizonte definido ({ficha['prazo_investimento']})")
    else:
        p.somar(0, "horizonte do investimento não informado")
    if _preenchido(ficha.get("expectativa_retorno")):
        p.somar(10, f"expectativa de retorno: {ficha['expectativa_retorno']}% a.a.")
    else:
        p.somar(0, "expectativa de retorno não informada")
    experiencia = ficha.get("experiencia_previa")
    if experiencia in ("ja_possui_imoveis", "investidor_experiente"):
        p.somar(15, f"experiência prévia ({experiencia})")
    elif _preenchido(experiencia):
        p.somar(5, f"experiência prévia ({experiencia})")
    else:
        p.somar(0, "experiência prévia não informada")


_PONTUADORES = {
    COMPRA: _pontuar_compra,
    ALUGUEL: _pontuar_aluguel,
    INVESTIMENTO: _pontuar_investimento,
}


class RegrasImobiliarias:
    def pontuar(self, intencao: str, ficha: Mapping[str, object]) -> Score:
        campos = CAMPOS.get(intencao)
        if campos is None:
            return Score(0, Classificacao.FRIO, (f"intenção desconhecida: {intencao}",))
        pontuacao = _Pontuacao()
        _PONTUADORES[intencao](ficha, pontuacao)
        preenchidos = sum(1 for c in campos if _preenchido(ficha.get(c)))
        pontuacao.somar(
            round(25 * preenchidos / len(campos)),
            f"completude da ficha ({preenchidos}/{len(campos)} campos)",
        )
        pontos = min(100, pontuacao.pontos)
        return Score(pontos, classificar(pontos), tuple(pontuacao.motivos))

    def qualificado(self, intencao: str, ficha: Mapping[str, object], score: Score) -> bool:
        essenciais = ESSENCIAIS.get(intencao)
        return bool(essenciais) and all(_preenchido(ficha.get(c)) for c in essenciais or ())
