from decimal import Decimal

import pytest

from sdr.domain.busca import CriteriosBusca, CriteriosInvalidosError
from sdr.domain.imovel import Finalidade, ImovelInvalidoError, TipoImovel, Zona
from tests.fabricas import criar_imovel


class TestImovel:
    @pytest.mark.parametrize(
        "sobrescritas",
        [
            {"preco": Decimal(0)},
            {"suites": 3, "quartos": 2},
            {"area_m2": Decimal(0)},
            {"estacao_metro": None},  # distância sem estação
            {"finalidade": Finalidade.ALUGUEL},  # aluguel com rentabilidade
        ],
    )
    def test_rejeita_dados_inconsistentes(self, sobrescritas: dict[str, object]) -> None:
        with pytest.raises(ImovelInvalidoError):
            criar_imovel(**sobrescritas)

    def test_custo_mensal_no_aluguel_inclui_aluguel(self) -> None:
        imovel = criar_imovel(
            finalidade=Finalidade.ALUGUEL, preco=Decimal(3000), rentabilidade_estimada_aa=None
        )
        assert imovel.custo_mensal == Decimal(4000)

    def test_custo_mensal_na_venda_e_so_condominio_e_iptu(self) -> None:
        assert criar_imovel().custo_mensal == Decimal(1000)

    def test_texto_semantico_tem_bairro_metro_e_comodidades(self) -> None:
        texto = criar_imovel().texto_semantico()
        assert "Saúde" in texto
        assert "estação Saúde" in texto
        assert "piscina" in texto
        assert "Aceita animais" in texto


class TestCriteriosBusca:
    def test_preco_min_maior_que_max_e_invalido(self) -> None:
        with pytest.raises(CriteriosInvalidosError):
            CriteriosBusca(preco_min=Decimal(900), preco_max=Decimal(100))

    def test_sobrescrever_mantem_inferidos_e_prioriza_explicitos(self) -> None:
        inferidos = CriteriosBusca(zonas=frozenset({Zona.SUL}), quartos_min=2)
        explicitos = CriteriosBusca(quartos_min=3, finalidade=Finalidade.ALUGUEL)

        combinados = inferidos.sobrescrever_com(explicitos)

        assert combinados.zonas == {Zona.SUL}
        assert combinados.quartos_min == 3
        assert combinados.finalidade is Finalidade.ALUGUEL

    @pytest.mark.parametrize(
        ("criterios", "esperado"),
        [
            (CriteriosBusca(), True),
            (CriteriosBusca(zonas=frozenset({Zona.SUL, Zona.OESTE})), True),
            (CriteriosBusca(zonas=frozenset({Zona.NORTE})), False),
            (CriteriosBusca(tipos=frozenset({TipoImovel.CASA})), False),
            (CriteriosBusca(preco_max=Decimal(700_000)), True),
            (CriteriosBusca(preco_max=Decimal(699_999)), False),
            (CriteriosBusca(quartos_min=3), False),
            (CriteriosBusca(distancia_max_metro_m=400), True),
            (CriteriosBusca(distancia_max_metro_m=399), False),
            (CriteriosBusca(bairros=frozenset({"saúde"})), True),
            (CriteriosBusca(aceita_pet=False), False),
            (CriteriosBusca(mobiliado=True), False),
        ],
    )
    def test_atende(self, criterios: CriteriosBusca, esperado: bool) -> None:
        assert criterios.atende(criar_imovel()) is esperado

    def test_sem_metro_nao_atende_filtro_de_distancia(self) -> None:
        imovel = criar_imovel(estacao_metro=None, distancia_metro_m=None)
        assert not CriteriosBusca(distancia_max_metro_m=5000).atende(imovel)
