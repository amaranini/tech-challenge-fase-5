"""Leitura do catálogo de imóveis em JSON (dados/imoveis.json da vertical) para entidades."""

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from sdr.verticals.imobiliario.catalogo.domain.imovel import Finalidade, Imovel, TipoImovel, Zona


def _decimal(valor: Any) -> Decimal | None:
    return None if valor is None else Decimal(str(valor))


def imovel_de_dict(d: dict[str, Any]) -> Imovel:
    return Imovel(
        id=d["id"],
        titulo=d["titulo"],
        tipo=TipoImovel(d["tipo"]),
        finalidade=Finalidade(d["finalidade"]),
        zona=Zona(d["zona"]),
        bairro=d["bairro"],
        preco=Decimal(str(d["preco"])),
        condominio=Decimal(str(d["condominio"])),
        iptu_mensal=Decimal(str(d["iptu_mensal"])),
        quartos=d["quartos"],
        suites=d["suites"],
        vagas=d["vagas"],
        area_m2=Decimal(str(d["area_m2"])),
        descricao=d["descricao"],
        estacao_metro=d.get("estacao_metro"),
        distancia_metro_m=d.get("distancia_metro_m"),
        comodidades=tuple(d.get("comodidades", ())),
        aceita_pet=d.get("aceita_pet", False),
        mobiliado=d.get("mobiliado", False),
        rentabilidade_estimada_aa=_decimal(d.get("rentabilidade_estimada_aa")),
    )


def ler_imoveis_json(caminho: Path) -> list[Imovel]:
    dados = json.loads(caminho.read_text(encoding="utf-8"))
    return [imovel_de_dict(d) for d in dados]
