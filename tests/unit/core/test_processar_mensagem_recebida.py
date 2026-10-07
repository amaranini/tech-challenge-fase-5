import pytest

from sdr.core.application.dto.mensagem_recebida import MensagemRecebida
from sdr.core.application.ports.llm import LLMIndisponivelError
from sdr.core.application.use_cases.processar_mensagem_recebida import (
    MensagemInvalidaError,
    ProcessarMensagemRecebida,
)
from sdr.core.domain.agente import Persona, RespostaAgente
from sdr.core.domain.conversa import Canal, Papel
from tests.apoio.fakes import (
    AgenteRoteirizado,
    CatalogoFake,
    ConversaRepositoryFake,
    LeadRepositoryFake,
    item,
)

PERSONA = Persona(
    nome="Ana",
    versao_prompt="ana_v1",
    prompt_sistema="...",
    padrao_codigo_item=r"\bA-\d+\b",
    mensagem_fallback="Já te respondo!",
)


class Cenario:
    def __init__(self, *respostas: RespostaAgente | Exception) -> None:
        self.leads = LeadRepositoryFake()
        self.conversas = ConversaRepositoryFake()
        self.agente = AgenteRoteirizado(*respostas)
        self.catalogo = CatalogoFake(item("A-1"), item("A-2"), item("A-3"))
        self.caso_de_uso = ProcessarMensagemRecebida(
            self.leads, self.conversas, self.agente, self.catalogo, persona=PERSONA
        )

    async def enviar(self, texto: str, remetente: str = "lead-1"):  # type: ignore[no-untyped-def]
        return await self.caso_de_uso.executar(MensagemRecebida(Canal.WEB, remetente, texto))


async def test_primeira_mensagem_cria_lead_conversa_e_grava_as_duas_mensagens() -> None:
    cenario = Cenario(RespostaAgente("Oi! Comprar ou alugar?", modelo="m1"))

    resultado = await cenario.enviar("oi")

    assert resultado.lead.remetente_id == "lead-1"
    assert len(cenario.leads.leads) == 1
    assert [(m.papel, m.texto) for m in cenario.conversas.mensagens] == [
        (Papel.LEAD, "oi"),
        (Papel.AGENTE, "Oi! Comprar ou alugar?"),
    ]
    assert resultado.resposta.metadados["prompt_versao"] == "ana_v1"
    assert resultado.resposta.metadados["modelo"] == "m1"


async def test_mensagens_seguintes_mantem_a_conversa_e_passam_o_historico() -> None:
    cenario = Cenario(RespostaAgente("Comprar ou alugar?"), RespostaAgente("Ótimo, qual região?"))

    primeiro = await cenario.enviar("oi")
    segundo = await cenario.enviar("quero comprar")

    assert primeiro.conversa.id == segundo.conversa.id
    assert len(cenario.leads.leads) == 1
    historico = cenario.agente.entradas[1].historico
    assert [m.texto for m in historico] == ["oi", "Comprar ou alugar?"]
    assert cenario.agente.entradas[1].texto == "quero comprar"


async def test_leads_diferentes_tem_conversas_separadas() -> None:
    cenario = Cenario(RespostaAgente("a"), RespostaAgente("b"))

    um = await cenario.enviar("oi", "lead-1")
    outro = await cenario.enviar("oi", "lead-2")

    assert um.conversa.id != outro.conversa.id
    assert cenario.agente.entradas[1].historico == []


async def test_itens_sugeridos_sao_os_codigos_citados_que_existem() -> None:
    cenario = Cenario(
        RespostaAgente("Olha o A-2 e o A-1!", itens_consultados=(item("A-1"), item("A-2")))
    )

    resultado = await cenario.enviar("quero opções")

    assert [i.id for i in resultado.itens_sugeridos] == ["A-2", "A-1"]
    assert [c["id"] for c in resultado.resposta.metadados["itens_citados"]] == ["A-2", "A-1"]


async def test_codigo_citado_em_turno_anterior_e_confirmado_no_catalogo() -> None:
    cenario = Cenario(RespostaAgente("O A-3 tem varanda, sim."))  # sem tool neste turno

    resultado = await cenario.enviar("o A-3 tem varanda?")

    assert [i.id for i in resultado.itens_sugeridos] == ["A-3"]


async def test_codigo_inventado_pede_correcao_ao_agente() -> None:
    cenario = Cenario(
        RespostaAgente("Tenho o A-99!"),
        RespostaAgente("Tenho o A-1!", itens_consultados=(item("A-1"),)),
    )

    resultado = await cenario.enviar("opções?")

    assert resultado.resposta.texto == "Tenho o A-1!"
    instrucao = cenario.agente.entradas[1].instrucao_adicional
    assert instrucao is not None
    assert "A-99" in instrucao


async def test_insistindo_em_codigo_inventado_responde_com_fallback() -> None:
    cenario = Cenario(RespostaAgente("Tenho o A-99!"), RespostaAgente("Tenho o A-98!"))

    resultado = await cenario.enviar("opções?")

    assert resultado.resposta.texto == "Já te respondo!"
    assert resultado.itens_sugeridos == ()
    assert resultado.resposta.metadados["fallback"] is True
    assert resultado.resposta.metadados["codigos_invalidos"] == ["A-98"]


async def test_falha_do_llm_preserva_a_mensagem_do_lead() -> None:
    cenario = Cenario(LLMIndisponivelError("fora do ar"))

    with pytest.raises(LLMIndisponivelError):
        await cenario.enviar("oi, tudo bem?")

    assert [m.texto for m in cenario.conversas.mensagens] == ["oi, tudo bem?"]


@pytest.mark.parametrize("texto", ["", "   ", "x" * 4001])
async def test_mensagem_invalida(texto: str) -> None:
    with pytest.raises(MensagemInvalidaError):
        await Cenario().enviar(texto)
