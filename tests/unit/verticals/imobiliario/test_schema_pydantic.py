from enum import StrEnum

from pydantic import BaseModel, Field

from sdr.verticals.imobiliario.qualificacao.adapters.schema_pydantic import SchemaPydantic


class Cor(StrEnum):
    AZUL = "azul"
    VERDE = "verde"


class FichaExemplo(BaseModel):
    quartos: int | None = Field(default=None, ge=0, le=10, description="quantos quartos")
    cor: Cor | None = Field(default=None, description="cor preferida")
    bairros: list[str] | None = None


SCHEMA = SchemaPydantic(FichaExemplo)


def test_campos_na_ordem_do_modelo() -> None:
    assert SCHEMA.campos == ("quartos", "cor", "bairros")


def test_schema_de_extracao_autocontido_e_sem_obrigatorios() -> None:
    schema = SCHEMA.schema_extracao()
    assert "$defs" not in str(schema)
    assert "$ref" not in str(schema)
    assert "required" not in schema
    assert "azul" in str(schema["properties"]["cor"])  # type: ignore[index]
    assert schema["properties"]["quartos"]["description"] == "quantos quartos"  # type: ignore[index]


def test_valida_campo_a_campo_descartando_so_os_invalidos() -> None:
    validos = SCHEMA.validar(
        {"quartos": "3", "cor": "roxo", "bairros": ["Saúde"], "inexistente": 1, "x": None}
    )
    assert validos == {"quartos": 3, "bairros": ["Saúde"]}


def test_normaliza_para_json() -> None:
    assert SCHEMA.validar({"cor": "azul", "quartos": 11}) == {"cor": "azul"}
