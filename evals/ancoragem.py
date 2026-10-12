"""Ancoragem dos imóveis sugeridos (vertical imobiliária): o código existe no catálogo e o
imóvel bate com a ficha do lead NAQUELE momento da conversa.

Regras de aderência (cada uma só vale se o campo da ficha estiver preenchido):
- finalidade: aluguel ⇒ imóvel de aluguel; compra/investimento ⇒ de venda;
- valor: preço ≤ preco_max (compra), ≤ ticket (investimento), ≤ aluguel_max (aluguel),
  com tolerância de 5% (a Lia pode mostrar algo "um pouco acima" dizendo isso);
- quartos: ≥ quartos pedidos;
- região: se a ficha cita uma zona (sul, oeste...), a zona do imóvel; se cita bairros,
  o bairro do imóvel aparece no texto (senão, não avalia);
- tipo: apartamento × casa × studio, quando informado.
"""

import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

TOLERANCIA_VALOR = 1.05
ZONAS = ("sul", "norte", "leste", "oeste", "centro")
CAMPO_VALOR = {"compra": "preco_max", "investimento": "ticket", "aluguel": "aluguel_max"}


def _sem_acento(texto: str) -> str:
    return unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode().casefold()


@dataclass
class Ancoragem:
    codigo: str
    existe: bool
    adere: bool | None = None  # None = não avaliado (item inexistente ou ficha vazia)
    motivos: list[str] = field(default_factory=list)


Checagem = tuple[bool, str | None]  # (avaliou?, motivo da não aderência)
NAO_AVALIA: Checagem = (False, None)


def _finalidade(item: Mapping[str, Any], intencao: str | None) -> Checagem:
    if intencao not in CAMPO_VALOR:
        return NAO_AVALIA
    finalidade = "aluguel" if intencao == "aluguel" else "venda"
    ok = item.get("finalidade") == finalidade
    return True, None if ok else f"finalidade {item.get('finalidade')} para {intencao}"


def _valor(item: Mapping[str, Any], intencao: str | None, ficha: Mapping[str, Any]) -> Checagem:
    limite = ficha.get(CAMPO_VALOR.get(intencao or "", ""))
    if not isinstance(limite, int | float) or limite <= 0:
        return NAO_AVALIA
    preco = float(item.get("preco") or 0)
    ok = preco <= limite * TOLERANCIA_VALOR
    return True, None if ok else f"preço {preco:,.0f} acima de {limite:,.0f}"


def _quartos(item: Mapping[str, Any], ficha: Mapping[str, Any]) -> Checagem:
    quartos = ficha.get("quartos")
    if not isinstance(quartos, int) or quartos <= 0:
        return NAO_AVALIA
    ok = int(item.get("quartos") or 0) >= quartos
    return True, None if ok else f"{item.get('quartos')} quartos (pediu {quartos})"


def _regiao(item: Mapping[str, Any], ficha: Mapping[str, Any]) -> Checagem:
    regiao = ficha.get("regiao")
    if not isinstance(regiao, str) or not regiao.strip():
        return NAO_AVALIA
    alvo = _sem_acento(regiao)
    if zonas := [z for z in ZONAS if z in alvo]:
        ok = item.get("zona") in zonas
        return True, None if ok else f"zona {item.get('zona')} (pediu {', '.join(zonas)})"
    if not item.get("bairro"):
        return NAO_AVALIA
    ok = _sem_acento(str(item["bairro"])) in alvo
    return True, None if ok else f"bairro {item['bairro']} fora de {regiao!r}"


def _tipo(item: Mapping[str, Any], ficha: Mapping[str, Any]) -> Checagem:
    tipo = ficha.get("tipo_imovel")
    if not isinstance(tipo, str) or not tipo:
        return NAO_AVALIA
    ok = _sem_acento(tipo) == _sem_acento(str(item.get("tipo", "")))
    return True, None if ok else f"tipo {item.get('tipo')} (pediu {tipo})"


def avaliar_item(
    codigo: str, item: Mapping[str, Any] | None, intencao: str | None, ficha: Mapping[str, Any]
) -> Ancoragem:
    if item is None:
        return Ancoragem(codigo, existe=False, motivos=["código não existe no catálogo"])
    checagens = [
        _finalidade(item, intencao),
        _valor(item, intencao, ficha),
        _quartos(item, ficha),
        _regiao(item, ficha),
        _tipo(item, ficha),
    ]
    motivos = [motivo for avaliou, motivo in checagens if avaliou and motivo]
    avaliados = any(avaliou for avaliou, _ in checagens)
    return Ancoragem(
        codigo, existe=True, adere=(not motivos) if avaliados else None, motivos=motivos
    )
