"""SchemaFicha (Protocol do core) implementado sobre um modelo Pydantic da vertical."""

from collections.abc import Mapping
from typing import Annotated, Any

from pydantic import BaseModel, TypeAdapter, ValidationError
from pydantic.fields import FieldInfo


def _resolver_refs(no: Any, definicoes: Mapping[str, Any]) -> Any:
    """Inlina `$ref` para `$defs` — schema autocontido, mais simples para o LLM."""
    if isinstance(no, dict):
        if "$ref" in no:
            alvo = definicoes[no["$ref"].split("/")[-1]]
            extras = {k: v for k, v in no.items() if k != "$ref"}
            return _resolver_refs({**alvo, **extras}, definicoes)
        return {k: _resolver_refs(v, definicoes) for k, v in no.items() if k != "$defs"}
    if isinstance(no, list):
        return [_resolver_refs(v, definicoes) for v in no]
    return no


def _tipo_com_restricoes(campo: FieldInfo) -> Any:
    """Annotated[tipo, *metadata] preserva as restrições do Field (ge, le, max_length...)."""
    if not campo.metadata:
        return campo.annotation
    return Annotated[campo.annotation, *campo.metadata]


class SchemaPydantic:
    """Cada campo é validado isoladamente: um valor inválido não derruba os demais."""

    def __init__(self, modelo: type[BaseModel]) -> None:
        self._modelo = modelo
        self._adaptadores: dict[str, TypeAdapter[Any]] = {
            nome: TypeAdapter(_tipo_com_restricoes(campo))
            for nome, campo in modelo.model_fields.items()
        }
        bruto = modelo.model_json_schema()
        self._schema: dict[str, Any] = _resolver_refs(bruto, bruto.get("$defs", {}))
        self._schema.pop("required", None)  # na extração, tudo é opcional
        self._schema.pop("title", None)

    @property
    def campos(self) -> tuple[str, ...]:
        return tuple(self._modelo.model_fields)

    def schema_extracao(self) -> Mapping[str, object]:
        return self._schema

    def validar(self, dados: Mapping[str, object]) -> dict[str, object]:
        validos: dict[str, object] = {}
        for nome, valor in dados.items():
            adaptador = self._adaptadores.get(nome)
            if adaptador is None or valor is None:
                continue
            try:
                validos[nome] = adaptador.dump_python(adaptador.validate_python(valor), mode="json")
            except ValidationError:
                continue
        return validos
