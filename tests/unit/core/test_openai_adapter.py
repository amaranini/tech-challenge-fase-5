"""Adapter OpenAI testado com um cliente fake (sem rede)."""

from types import SimpleNamespace
from typing import Any

import httpx
import openai
import pytest

from sdr.core.adapters.outbound.llm.openai_adapter import LLMOpenAI, para_openai
from sdr.core.application.ports.llm import (
    ChamadaFerramenta,
    DefinicaoFerramenta,
    LLMIndisponivelError,
    MensagemLLM,
    PapelLLM,
)


class ClienteFake:
    def __init__(self, resposta: Any = None, erro: Exception | None = None) -> None:
        self.parametros: dict[str, Any] = {}
        self._resposta, self._erro = resposta, erro
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._criar))

    async def _criar(self, **parametros: Any) -> Any:
        self.parametros = parametros
        if self._erro:
            raise self._erro
        return self._resposta


def resposta_openai(conteudo: str | None, tool_calls: list[Any] | None = None) -> Any:
    mensagem = SimpleNamespace(content=conteudo, tool_calls=tool_calls)
    return SimpleNamespace(
        choices=[SimpleNamespace(message=mensagem)],
        model="gpt-teste",
        usage=SimpleNamespace(prompt_tokens=12, completion_tokens=7),
    )


def tool_call(id_: str, nome: str, argumentos: str) -> Any:
    return SimpleNamespace(
        id=id_, type="function", function=SimpleNamespace(name=nome, arguments=argumentos)
    )


def llm(cliente: ClienteFake, temperatura: float | None = 0.3) -> LLMOpenAI:
    return LLMOpenAI(api_key=None, modelo="gpt-teste", temperatura=temperatura, cliente=cliente)  # type: ignore[arg-type]


def test_conversao_de_mensagens() -> None:
    chamada = ChamadaFerramenta("c1", "buscar", {"texto": "apê"})
    assert para_openai(MensagemLLM(PapelLLM.SISTEMA, "s")) == {"role": "system", "content": "s"}
    assert para_openai(MensagemLLM(PapelLLM.ASSISTENTE, "", (chamada,))) == {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "c1",
                "type": "function",
                "function": {"name": "buscar", "arguments": '{"texto": "apê"}'},
            }
        ],
    }
    assert para_openai(MensagemLLM(PapelLLM.FERRAMENTA, "{}", id_chamada="c1")) == {
        "role": "tool",
        "tool_call_id": "c1",
        "content": "{}",
    }


async def test_gera_texto_e_le_uso_de_tokens() -> None:
    cliente = ClienteFake(resposta_openai("Oi!"))

    resposta = await llm(cliente).gerar([MensagemLLM(PapelLLM.USUARIO, "oi")])

    assert (resposta.conteudo, resposta.modelo) == ("Oi!", "gpt-teste")
    assert (resposta.tokens_entrada, resposta.tokens_saida) == (12, 7)
    assert cliente.parametros["temperature"] == 0.3
    assert "tools" not in cliente.parametros


async def test_envia_ferramentas_e_le_chamadas() -> None:
    cliente = ClienteFake(
        resposta_openai(
            None, [tool_call("c1", "buscar", '{"texto": "apto"}'), tool_call("c2", "x", "{oops")]
        )
    )
    definicao = DefinicaoFerramenta("buscar", "Busca", {"type": "object"})

    resposta = await llm(cliente, temperatura=None).gerar([], [definicao])

    assert cliente.parametros["tools"][0]["function"]["name"] == "buscar"
    assert cliente.parametros["tool_choice"] == "auto"
    assert "temperature" not in cliente.parametros
    assert resposta.conteudo == ""
    assert [(c.nome, dict(c.argumentos)) for c in resposta.chamadas] == [
        ("buscar", {"texto": "apto"}),
        ("x", {}),  # JSON inválido não derruba o turno
    ]


async def test_forcar_texto_desliga_tools() -> None:
    cliente = ClienteFake(resposta_openai("ok"))
    definicao = DefinicaoFerramenta("buscar", "Busca", {"type": "object"})

    await llm(cliente).gerar([], [definicao], forcar_texto=True)

    assert cliente.parametros["tool_choice"] == "none"


async def test_sem_chave_fica_indisponivel() -> None:
    with pytest.raises(LLMIndisponivelError, match="OPENAI_API_KEY"):
        await LLMOpenAI(api_key=None, modelo="m").gerar([])


async def test_erro_de_conexao_vira_indisponibilidade() -> None:
    erro = openai.APIConnectionError(request=httpx.Request("POST", "https://api.openai.com"))
    with pytest.raises(LLMIndisponivelError, match="APIConnectionError"):
        await llm(ClienteFake(erro=erro)).gerar([])


async def test_saida_estruturada_usa_json_schema_e_le_o_json() -> None:
    cliente = ClienteFake(resposta_openai('{"intencao": "compra", "confianca": 0.9}'))
    schema = {"type": "object", "properties": {"intencao": {"type": "string"}}}

    resposta = await llm(cliente).gerar_estruturado([], schema, "roteador")

    assert resposta.dados == {"intencao": "compra", "confianca": 0.9}
    assert cliente.parametros["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "roteador", "schema": schema, "strict": False},
    }
    assert cliente.parametros["temperature"] == 0


@pytest.mark.parametrize("conteudo", ["não é json", "[1, 2]", None])
async def test_saida_estruturada_invalida_vira_dicionario_vazio(conteudo: str | None) -> None:
    resposta = await llm(ClienteFake(resposta_openai(conteudo))).gerar_estruturado([], {}, "x")
    assert resposta.dados == {}
