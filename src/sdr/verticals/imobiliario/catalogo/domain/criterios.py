from dataclasses import dataclass, field, fields, replace
from decimal import Decimal

from sdr.verticals.imobiliario.catalogo.domain.imovel import Finalidade, Imovel, TipoImovel, Zona


class CriteriosInvalidosError(ValueError):
    pass


@dataclass(frozen=True)
class CriteriosBusca:
    """Filtros estruturados da busca. `None`/vazio = sem restrição."""

    finalidade: Finalidade | None = None
    tipos: frozenset[TipoImovel] = field(default_factory=frozenset)
    zonas: frozenset[Zona] = field(default_factory=frozenset)
    bairros: frozenset[str] = field(default_factory=frozenset)
    preco_min: Decimal | None = None
    preco_max: Decimal | None = None
    quartos_min: int | None = None
    vagas_min: int | None = None
    area_min_m2: Decimal | None = None
    distancia_max_metro_m: int | None = None
    aceita_pet: bool | None = None
    mobiliado: bool | None = None

    def __post_init__(self) -> None:
        if (
            self.preco_min is not None
            and self.preco_max is not None
            and self.preco_min > self.preco_max
        ):
            raise CriteriosInvalidosError("preço mínimo maior que o máximo")
        for nome in ("quartos_min", "vagas_min", "distancia_max_metro_m"):
            valor = getattr(self, nome)
            if valor is not None and valor < 0:
                raise CriteriosInvalidosError(f"{nome} não pode ser negativo")

    def sobrescrever_com(self, outros: "CriteriosBusca") -> "CriteriosBusca":
        """Combina critérios: o que estiver definido em `outros` prevalece."""
        definidos = {
            f.name: getattr(outros, f.name)
            for f in fields(outros)
            if getattr(outros, f.name) not in (None, frozenset())
        }
        return replace(self, **definidos)

    def atende(self, imovel: Imovel) -> bool:
        """Regra de referência dos filtros (usada por fakes/adapters em memória)."""
        bairros = {b.casefold() for b in self.bairros}
        return all(
            (
                self.finalidade is None or imovel.finalidade is self.finalidade,
                not self.tipos or imovel.tipo in self.tipos,
                not self.zonas or imovel.zona in self.zonas,
                not bairros or imovel.bairro.casefold() in bairros,
                self.preco_min is None or imovel.preco >= self.preco_min,
                self.preco_max is None or imovel.preco <= self.preco_max,
                self.quartos_min is None or imovel.quartos >= self.quartos_min,
                self.vagas_min is None or imovel.vagas >= self.vagas_min,
                self.area_min_m2 is None or imovel.area_m2 >= self.area_min_m2,
                self.distancia_max_metro_m is None
                or (
                    imovel.distancia_metro_m is not None
                    and imovel.distancia_metro_m <= self.distancia_max_metro_m
                ),
                self.aceita_pet is None or imovel.aceita_pet is self.aceita_pet,
                self.mobiliado is None or imovel.mobiliado is self.mobiliado,
            )
        )


@dataclass(frozen=True)
class ImovelEncontrado:
    imovel: Imovel
    similaridade: float | None = None  # 0..1 quando houve busca semântica
