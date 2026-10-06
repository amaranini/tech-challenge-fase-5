from decimal import Decimal

import pytest

from sdr.verticals.imobiliario.catalogo.adapters.interpretador_regras import InterpretadorRegras
from sdr.verticals.imobiliario.catalogo.domain.criterios import CriteriosBusca
from sdr.verticals.imobiliario.catalogo.domain.imovel import Finalidade, TipoImovel, Zona

interpretador = InterpretadorRegras(distancia_metro_padrao_m=1000)


def test_frase_do_criterio_de_aceite() -> None:
    criterios = interpretador.interpretar("apê 2 quartos zona sul até 800 mil perto do metrô")

    assert criterios == CriteriosBusca(
        finalidade=Finalidade.VENDA,
        tipos=frozenset({TipoImovel.APARTAMENTO}),
        zonas=frozenset({Zona.SUL}),
        preco_max=Decimal(800_000),
        quartos_min=2,
        distancia_max_metro_m=1000,
    )


@pytest.mark.parametrize(
    ("texto", "campo", "esperado"),
    [
        ("quero alugar um studio", "finalidade", Finalidade.ALUGUEL),
        ("busco algo para investir", "finalidade", Finalidade.VENDA),
        ("investir num imóvel para alugar", "finalidade", Finalidade.VENDA),
        ("alugar mobiliado", "mobiliado", True),
        ("até 3 mil por mês", "finalidade", Finalidade.ALUGUEL),
        ("até 3 mil por mês", "preco_max", Decimal(3000)),
        ("até R$ 1.200.000", "preco_max", Decimal(1_200_000)),
        ("no máximo 1,5 milhão", "preco_max", Decimal(1_500_000)),
        ("a partir de 500k", "preco_min", Decimal(500_000)),
        ("entre 500 e 700 mil", "preco_min", Decimal(500_000)),
        ("entre 500 e 700 mil", "preco_max", Decimal(700_000)),
        ("três dormitórios", "quartos_min", 3),
        ("2 vagas", "vagas_min", 2),
        ("com garagem", "vagas_min", 1),
        ("a 500 m do metrô", "distancia_max_metro_m", 500),
        ("até 1,5 km da estação", "distancia_max_metro_m", 1500),
        ("tenho um cachorro", "aceita_pet", True),
        ("kitnet no centro", "tipos", frozenset({TipoImovel.STUDIO})),
        ("ZN ou zona leste", "zonas", frozenset({Zona.NORTE, Zona.LESTE})),
    ],
)
def test_extrai_campo(texto: str, campo: str, esperado: object) -> None:
    assert getattr(interpretador.interpretar(texto), campo) == esperado


def test_casa_verde_e_bairro_nao_tipo() -> None:
    assert interpretador.interpretar("apto na Casa Verde").tipos == {TipoImovel.APARTAMENTO}


def test_distancia_nao_e_confundida_com_preco() -> None:
    criterios = interpretador.interpretar("até 1 km do metrô")
    assert criterios.preco_max is None
    assert criterios.distancia_max_metro_m == 1000


def test_texto_sem_filtros_reconheciveis() -> None:
    assert interpretador.interpretar("um lugar tranquilo e arborizado") == CriteriosBusca()
