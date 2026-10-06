"""Vocabulário de filtros da vertical imobiliária (validação na borda).

Compartilhado pela rota HTTP e pelo CatalogoPort — e, na Etapa C, pela tool do agente.
"""

from decimal import Decimal

from pydantic import BaseModel, Field

from sdr.verticals.imobiliario.catalogo.domain.criterios import CriteriosBusca
from sdr.verticals.imobiliario.catalogo.domain.imovel import Finalidade, TipoImovel, Zona


class Filtros(BaseModel):
    finalidade: Finalidade | None = None
    tipos: list[TipoImovel] = Field(default_factory=list)
    zonas: list[Zona] = Field(default_factory=list)
    bairros: list[str] = Field(default_factory=list)
    preco_min: Decimal | None = Field(default=None, ge=0)
    preco_max: Decimal | None = Field(default=None, ge=0)
    quartos_min: int | None = Field(default=None, ge=0)
    vagas_min: int | None = Field(default=None, ge=0)
    area_min_m2: Decimal | None = Field(default=None, ge=0)
    distancia_max_metro_m: int | None = Field(default=None, ge=0)
    aceita_pet: bool | None = None
    mobiliado: bool | None = None

    def para_dominio(self) -> CriteriosBusca:
        return CriteriosBusca(
            finalidade=self.finalidade,
            tipos=frozenset(self.tipos),
            zonas=frozenset(self.zonas),
            bairros=frozenset(self.bairros),
            preco_min=self.preco_min,
            preco_max=self.preco_max,
            quartos_min=self.quartos_min,
            vagas_min=self.vagas_min,
            area_min_m2=self.area_min_m2,
            distancia_max_metro_m=self.distancia_max_metro_m,
            aceita_pet=self.aceita_pet,
            mobiliado=self.mobiliado,
        )

    @classmethod
    def de_dominio(cls, c: CriteriosBusca) -> "Filtros":
        return cls(
            finalidade=c.finalidade,
            tipos=sorted(c.tipos),
            zonas=sorted(c.zonas),
            bairros=sorted(c.bairros),
            preco_min=c.preco_min,
            preco_max=c.preco_max,
            quartos_min=c.quartos_min,
            vagas_min=c.vagas_min,
            area_min_m2=c.area_min_m2,
            distancia_max_metro_m=c.distancia_max_metro_m,
            aceita_pet=c.aceita_pet,
            mobiliado=c.mobiliado,
        )
