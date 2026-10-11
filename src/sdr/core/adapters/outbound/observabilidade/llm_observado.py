"""LLMPort que registra cada chamada como GERAÇÃO no rastro atual (modelo, entrada, saída,
tokens, latência) e delega ao LLM real. Genérico: não conhece o fornecedor de
observabilidade (fala com o RastreioPort) nem o de LLM."""

from collections.abc import Mapping, Sequence

from sdr.core.application.ports.llm import (
    DefinicaoFerramenta,
    LLMPort,
    MensagemLLM,
    RespostaEstruturada,
    RespostaLLM,
)
from sdr.core.application.ports.observabilidade import RastreioPort


def _entrada(mensagens: Sequence[MensagemLLM]) -> list[dict[str, object]]:
    saida: list[dict[str, object]] = []
    for m in mensagens:
        item: dict[str, object] = {"role": m.papel.value, "content": m.conteudo}
        if m.chamadas:
            item["tool_calls"] = [
                {"name": c.nome, "arguments": dict(c.argumentos)} for c in m.chamadas
            ]
        if m.id_chamada:
            item["tool_call_id"] = m.id_chamada
        saida.append(item)
    return saida


class LLMObservado:
    def __init__(self, llm: LLMPort, rastreio: RastreioPort, nome: str) -> None:
        self._llm = llm
        self._rastreio = rastreio
        self._nome = nome

    @property
    def modelo(self) -> str:
        return self._llm.modelo

    async def gerar(
        self,
        mensagens: Sequence[MensagemLLM],
        ferramentas: Sequence[DefinicaoFerramenta] = (),
        forcar_texto: bool = False,
    ) -> RespostaLLM:
        parametros = {"ferramentas": [f.nome for f in ferramentas], "forcar_texto": forcar_texto}
        with self._rastreio.geracao(
            self._nome, modelo=self._llm.modelo, entrada=_entrada(mensagens), parametros=parametros
        ) as obs:
            try:
                resposta = await self._llm.gerar(mensagens, ferramentas, forcar_texto)
            except Exception as erro:
                obs.erro = f"{type(erro).__name__}: {erro}"[:200]
                raise
            obs.saida = resposta.conteudo or [
                {"ferramenta": c.nome, "argumentos": dict(c.argumentos)} for c in resposta.chamadas
            ]
            obs.modelo = resposta.modelo
            obs.tokens_entrada, obs.tokens_saida = resposta.tokens_entrada, resposta.tokens_saida
            return resposta

    async def gerar_estruturado(
        self,
        mensagens: Sequence[MensagemLLM],
        schema: Mapping[str, object],
        nome: str,
    ) -> RespostaEstruturada:
        with self._rastreio.geracao(
            self._nome,
            modelo=self._llm.modelo,
            entrada=_entrada(mensagens),
            parametros={"saida_estruturada": nome},
        ) as obs:
            try:
                resposta = await self._llm.gerar_estruturado(mensagens, schema, nome)
            except Exception as erro:
                obs.erro = f"{type(erro).__name__}: {erro}"[:200]
                raise
            obs.saida = dict(resposta.dados)
            obs.modelo = resposta.modelo
            obs.tokens_entrada, obs.tokens_saida = resposta.tokens_entrada, resposta.tokens_saida
            return resposta
