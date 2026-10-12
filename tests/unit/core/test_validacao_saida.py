"""Validação de saída no turno (ADR 015): nada fora da base, nenhuma promessa indevida."""

from sdr.core.domain.agente import RespostaAgente
from sdr.core.domain.eventos import TipoEvento
from tests.apoio.fakes import item
from tests.unit.core.test_processar_turno import Cenario


def _reprovacoes(cenario: Cenario) -> list[dict[str, object]]:
    return [
        dict(e.payload) for e in cenario.eventos.eventos if e.tipo is TipoEvento.RESPOSTA_REPROVADA
    ]


async def test_promessa_de_desconto_pede_correcao_e_registra_evento() -> None:
    cenario = Cenario(
        RespostaAgente("Consigo um desconto de 10% no A-1!", itens_consultados=(item("A-1"),)),
        RespostaAgente("O A-1 sai pelo valor do anúncio; negociação é com a equipe."),
    )
    cenario.chega("tem desconto?")

    resultado = await cenario.processar()

    assert resultado is not None
    assert resultado.resposta.texto.startswith("O A-1 sai pelo valor")
    instrucao = cenario.agente.entradas[1].instrucao_adicional
    assert instrucao is not None
    assert "desconto" in instrucao
    assert _reprovacoes(cenario) == [
        {"codigos_invalidos": [], "promessas": ["desconto"], "fallback": False}
    ]


async def test_promessa_insistente_cai_na_resposta_segura() -> None:
    cenario = Cenario(
        RespostaAgente("Garanto que o dono aceita!"), RespostaAgente("Garanto que aceita sim!")
    )
    cenario.chega("o dono aceita 700?")

    resultado = await cenario.processar()

    assert resultado is not None
    assert resultado.resposta.texto == "Já te respondo!"
    assert resultado.resposta.metadados["fallback"] is True
    assert _reprovacoes(cenario) == [
        {"codigos_invalidos": [], "promessas": ["garanto_que"], "fallback": True}
    ]


async def test_codigo_e_promessa_juntos_na_mesma_correcao() -> None:
    cenario = Cenario(
        RespostaAgente("O A-99 tem desconto de 5%"),
        RespostaAgente("Tenho o A-2!", itens_consultados=(item("A-2"),)),
    )
    cenario.chega("opções?")

    await cenario.processar()

    instrucao = cenario.agente.entradas[1].instrucao_adicional or ""
    assert "A-99" in instrucao
    assert "desconto" in instrucao


async def test_resposta_valida_nao_gera_evento() -> None:
    cenario = Cenario(RespostaAgente("Tenho o A-1!", itens_consultados=(item("A-1"),)))
    cenario.chega("opções?")
    await cenario.processar()
    assert _reprovacoes(cenario) == []


async def test_cpf_nao_chega_ao_agente() -> None:
    cenario = Cenario(RespostaAgente("Anotado!"))
    cenario.chega("meu cpf é 123.456.789-09")

    await cenario.processar()

    texto = cenario.agente.entradas[0].texto
    assert "123.456.789-09" not in texto
    assert "***.***.***-**" in texto
