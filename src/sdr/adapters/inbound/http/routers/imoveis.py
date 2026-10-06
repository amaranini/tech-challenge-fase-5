from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from sdr.adapters.inbound.http.dependencias import obter_buscar_imoveis
from sdr.application.use_cases.buscar_imoveis import BuscarImoveis, ConsultaImoveis
from sdr.domain.busca import CriteriosBusca, CriteriosInvalidosError, ImovelEncontrado
from sdr.domain.imovel import Finalidade, TipoImovel, Zona

router = APIRouter(prefix="/imoveis", tags=["imóveis"])


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


class BuscaRequest(BaseModel):
    texto: str | None = Field(
        default=None,
        max_length=500,
        examples=["apê 2 quartos zona sul até 800 mil perto do metrô"],
    )
    filtros: Filtros = Field(default_factory=Filtros)
    limite: int = Field(default=5, ge=1, le=20)
    interpretar_texto: bool = Field(
        default=True,
        description="Extrai filtros do texto; filtros explícitos prevalecem sobre os inferidos.",
    )


class ImovelResposta(BaseModel):
    id: str
    titulo: str
    tipo: TipoImovel
    finalidade: Finalidade
    zona: Zona
    bairro: str
    preco: Decimal
    condominio: Decimal
    iptu_mensal: Decimal
    quartos: int
    suites: int
    vagas: int
    area_m2: Decimal
    estacao_metro: str | None
    distancia_metro_m: int | None
    comodidades: list[str]
    aceita_pet: bool
    mobiliado: bool
    rentabilidade_estimada_aa: Decimal | None
    descricao: str
    similaridade: float | None

    @classmethod
    def de_dominio(cls, encontrado: ImovelEncontrado) -> "ImovelResposta":
        i = encontrado.imovel
        return cls(
            id=i.id,
            titulo=i.titulo,
            tipo=i.tipo,
            finalidade=i.finalidade,
            zona=i.zona,
            bairro=i.bairro,
            preco=i.preco,
            condominio=i.condominio,
            iptu_mensal=i.iptu_mensal,
            quartos=i.quartos,
            suites=i.suites,
            vagas=i.vagas,
            area_m2=i.area_m2,
            estacao_metro=i.estacao_metro,
            distancia_metro_m=i.distancia_metro_m,
            comodidades=list(i.comodidades),
            aceita_pet=i.aceita_pet,
            mobiliado=i.mobiliado,
            rentabilidade_estimada_aa=i.rentabilidade_estimada_aa,
            descricao=i.descricao,
            similaridade=encontrado.similaridade,
        )


class BuscaResposta(BaseModel):
    criterios_aplicados: Filtros
    total: int
    resultados: list[ImovelResposta]


@router.post("/busca", response_model=BuscaResposta)
async def buscar(
    requisicao: BuscaRequest,
    buscar_imoveis: Annotated[BuscarImoveis, Depends(obter_buscar_imoveis)],
) -> BuscaResposta:
    try:
        consulta = ConsultaImoveis(
            texto=requisicao.texto,
            criterios=requisicao.filtros.para_dominio(),
            limite=requisicao.limite,
            interpretar_texto=requisicao.interpretar_texto,
        )
        resultado = await buscar_imoveis.executar(consulta)
    except CriteriosInvalidosError as erro:
        raise HTTPException(422, str(erro)) from erro

    return BuscaResposta(
        criterios_aplicados=Filtros.de_dominio(resultado.criterios_aplicados),
        total=len(resultado.imoveis),
        resultados=[ImovelResposta.de_dominio(e) for e in resultado.imoveis],
    )
