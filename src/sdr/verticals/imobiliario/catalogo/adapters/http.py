from decimal import Decimal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from sdr.verticals.imobiliario.catalogo.adapters.esquema_filtros import Filtros
from sdr.verticals.imobiliario.catalogo.application.use_cases.buscar_imoveis import (
    BuscarImoveis,
    ConsultaImoveis,
)
from sdr.verticals.imobiliario.catalogo.domain.criterios import (
    CriteriosInvalidosError,
    ImovelEncontrado,
)
from sdr.verticals.imobiliario.catalogo.domain.imovel import Finalidade, TipoImovel, Zona


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


def criar_router(buscar_imoveis: BuscarImoveis) -> APIRouter:
    """Rotas da vertical; o caso de uso chega já montado pelo PackImobiliario."""
    router = APIRouter(prefix="/imoveis", tags=["imóveis"])

    @router.post("/busca", response_model=BuscaResposta)
    async def buscar(requisicao: BuscaRequest) -> BuscaResposta:
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

    return router
