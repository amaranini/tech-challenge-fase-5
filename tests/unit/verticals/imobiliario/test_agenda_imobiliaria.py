"""Agenda da imobiliária: quem atende cada lead e o que se agenda em cada intenção."""

import pytest

from sdr.verticals.imobiliario.agenda.adapters.agenda_imobiliaria import (
    REUNIAO_ESPECIALISTA,
    TIPOS_AGENDAMENTO,
    VISITA_IMOVEL,
    ler_responsaveis,
)
from sdr.verticals.imobiliario.agenda.domain.atribuicao import RegraAtribuicaoImobiliaria
from sdr.verticals.imobiliario.pack import RESPONSAVEIS

RESPONSAVEIS_SEED = ler_responsaveis(RESPONSAVEIS)
POR_ESPECIALIDADE = {r.especialidades[0]: r.nome for r in RESPONSAVEIS_SEED}
REGRA = RegraAtribuicaoImobiliaria({"Moema": "sul", "Pinheiros": "oeste", "Santana": "norte"})


def nomes(intencao: str, regiao: str | None = None) -> list[str]:
    ficha = {"regiao": regiao} if regiao else {}
    return [r.nome for r in REGRA.ordenar(intencao, ficha, RESPONSAVEIS_SEED)]


def test_seed_tem_os_quatro_responsaveis() -> None:
    assert POR_ESPECIALIDADE == {
        "compra:sul": "Rafael Souza",
        "compra:oeste": "Camila Ito",
        "locacao": "Bruno Lima",
        "investimentos": "Marina Costa",
    }


@pytest.mark.parametrize(
    ("regiao", "esperado"),
    [
        ("zona sul", ["Rafael Souza"]),
        ("Zona Oeste, perto do metrô", ["Camila Ito"]),
        ("Moema ou Vila Mariana", ["Rafael Souza"]),
        ("pinheiros", ["Camila Ito"]),
        ("Santana", ["Rafael Souza", "Camila Ito"]),  # outra zona: os dois, sul primeiro
        (None, ["Rafael Souza", "Camila Ito"]),
    ],
)
def test_compra_por_regiao(regiao: str | None, esperado: list[str]) -> None:
    assert nomes("compra", regiao) == esperado


def test_aluguel_e_investimento() -> None:
    assert nomes("aluguel", "zona sul") == ["Bruno Lima"]
    assert nomes("investimento") == ["Marina Costa"]
    assert nomes("outra") == []


def test_tipos_de_agendamento_por_intencao() -> None:
    assert TIPOS_AGENDAMENTO["compra"] is VISITA_IMOVEL
    assert TIPOS_AGENDAMENTO["aluguel"] is VISITA_IMOVEL
    assert TIPOS_AGENDAMENTO["investimento"] is REUNIAO_ESPECIALISTA
    assert VISITA_IMOVEL.modalidade_unica == "presencial"
    assert set(REUNIAO_ESPECIALISTA.modalidades) == {"online", "escritorio"}
