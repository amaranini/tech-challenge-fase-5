"""AgenteConversacionalPort com LangGraph.

Grafo: START → agente ⇄ ferramentas → END. O nó `agente` chama o LLMPort (nosso, não um
modelo LangChain) e o nó `ferramentas` executa as tools da vertical, que chamam PORTS.
A memória da conversa NÃO fica no LangGraph (sem checkpointer): o histórico vem do
ConversaRepository, a cada turno, via EntradaAgente — fonte única da verdade é o banco.
"""

import operator
from collections.abc import Sequence
from datetime import datetime
from typing import Annotated, TypedDict, cast
from zoneinfo import ZoneInfo

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from sdr.core.application.ports.agente import EntradaAgente
from sdr.core.application.ports.ferramenta import Ferramenta, ResultadoFerramenta
from sdr.core.application.ports.llm import LLMPort, MensagemLLM, PapelLLM
from sdr.core.domain.agente import (
    ChamadaFerramentaRegistrada,
    Persona,
    RespostaAgente,
    unicos_por_id,
)
from sdr.core.domain.catalogo import ItemCatalogo
from sdr.core.domain.conversa import Mensagem, Papel

FUSO_PADRAO = ZoneInfo("America/Sao_Paulo")


class EstadoAgente(TypedDict):
    mensagens: Annotated[list[MensagemLLM], operator.add]
    itens: Annotated[list[ItemCatalogo], operator.add]
    chamadas: Annotated[list[ChamadaFerramentaRegistrada], operator.add]
    tokens_entrada: Annotated[int, operator.add]
    tokens_saida: Annotated[int, operator.add]
    passos: int
    modelo: str | None


def _nota_itens_citados(mensagem: Mensagem) -> str | None:
    """Lembra o LLM do que ele já apresentou (códigos + resumo), sem re-chamar a tool."""
    citados = mensagem.metadados.get("itens_citados")
    if not isinstance(citados, list) or not citados:
        return None
    linhas = [
        f"- {c.get('id')}: {c.get('resumo', c.get('titulo', ''))}"
        for c in citados
        if isinstance(c, dict)
    ]
    return "[Contexto interno] Itens apresentados na sua mensagem anterior:\n" + "\n".join(linhas)


class AgenteLangGraph:
    def __init__(
        self,
        llm: LLMPort,
        persona: Persona,
        ferramentas: Sequence[Ferramenta],
        max_passos: int = 4,
        fuso: ZoneInfo = FUSO_PADRAO,
    ) -> None:
        self._llm = llm
        self._persona = persona
        self._ferramentas = {f.definicao.nome: f for f in ferramentas}
        self._max_passos = max_passos
        self._fuso = fuso
        self._grafo = self._construir_grafo()

    def _construir_grafo(
        self,
    ) -> CompiledStateGraph[EstadoAgente, None, EstadoAgente, EstadoAgente]:
        grafo = StateGraph(EstadoAgente)
        # Os stubs do LangGraph não reconhecem TypedDict no Protocol genérico de add_node.
        grafo.add_node("agente", self._no_agente)  # type: ignore[call-overload]
        grafo.add_node("ferramentas", self._no_ferramentas)  # type: ignore[call-overload]
        grafo.add_edge(START, "agente")
        grafo.add_conditional_edges(
            "agente", self._proximo, {"ferramentas": "ferramentas", END: END}
        )
        grafo.add_edge("ferramentas", "agente")
        return grafo.compile()

    async def responder(self, entrada: EntradaAgente) -> RespostaAgente:
        inicial: EstadoAgente = {
            "mensagens": self._mensagens_iniciais(entrada),
            "itens": [],
            "chamadas": [],
            "tokens_entrada": 0,
            "tokens_saida": 0,
            "passos": 0,
            "modelo": None,
        }
        final = cast(
            EstadoAgente,
            await self._grafo.ainvoke(
                inicial, config={"recursion_limit": 2 * self._max_passos + 4}
            ),
        )
        texto = final["mensagens"][-1].conteudo.strip() or self._persona.mensagem_fallback
        return RespostaAgente(
            texto=texto,
            itens_consultados=unicos_por_id(final["itens"]),
            chamadas=tuple(final["chamadas"]),
            modelo=final["modelo"],
            tokens_entrada=final["tokens_entrada"],
            tokens_saida=final["tokens_saida"],
        )

    def _mensagens_iniciais(self, entrada: EntradaAgente) -> list[MensagemLLM]:
        momento = datetime.now(self._fuso)
        contexto = [f"Data e hora atuais: {momento:%d/%m/%Y %H:%M} ({self._fuso.key})."]
        if entrada.lead.nome:
            contexto.append(f"Nome do lead: {entrada.lead.nome}.")
        mensagens = [
            MensagemLLM(PapelLLM.SISTEMA, self._persona.prompt_sistema),
            MensagemLLM(PapelLLM.SISTEMA, " ".join(contexto)),
        ]
        for anterior in entrada.historico:
            if anterior.papel is Papel.LEAD:
                mensagens.append(MensagemLLM(PapelLLM.USUARIO, anterior.texto))
                continue
            mensagens.append(MensagemLLM(PapelLLM.ASSISTENTE, anterior.texto))
            if nota := _nota_itens_citados(anterior):
                mensagens.append(MensagemLLM(PapelLLM.SISTEMA, nota))
        mensagens.append(MensagemLLM(PapelLLM.USUARIO, entrada.texto))
        if entrada.instrucao_adicional:
            mensagens.append(MensagemLLM(PapelLLM.SISTEMA, entrada.instrucao_adicional))
        return mensagens

    async def _no_agente(self, estado: EstadoAgente) -> dict[str, object]:
        esgotou = estado["passos"] >= self._max_passos
        resposta = await self._llm.gerar(
            estado["mensagens"],
            [f.definicao for f in self._ferramentas.values()],
            forcar_texto=esgotou,
        )
        chamadas = () if esgotou else resposta.chamadas
        return {
            "mensagens": [MensagemLLM(PapelLLM.ASSISTENTE, resposta.conteudo, chamadas)],
            "tokens_entrada": resposta.tokens_entrada,
            "tokens_saida": resposta.tokens_saida,
            "passos": estado["passos"] + 1,
            "modelo": resposta.modelo or estado["modelo"],
        }

    @staticmethod
    def _proximo(estado: EstadoAgente) -> str:
        return "ferramentas" if estado["mensagens"][-1].chamadas else END

    async def _no_ferramentas(self, estado: EstadoAgente) -> dict[str, object]:
        respostas: list[MensagemLLM] = []
        itens: list[ItemCatalogo] = []
        registros: list[ChamadaFerramentaRegistrada] = []
        for chamada in estado["mensagens"][-1].chamadas:
            ferramenta = self._ferramentas.get(chamada.nome)
            if ferramenta is None:
                resultado = ResultadoFerramenta(
                    conteudo=f'{{"erro": "ferramenta {chamada.nome} não existe"}}',
                    erro="ferramenta inexistente",
                )
            else:
                resultado = await ferramenta.executar(chamada.argumentos)
            respostas.append(
                MensagemLLM(PapelLLM.FERRAMENTA, resultado.conteudo, id_chamada=chamada.id)
            )
            itens.extend(resultado.itens)
            registros.append(
                ChamadaFerramentaRegistrada(chamada.nome, dict(chamada.argumentos), resultado.erro)
            )
        return {"mensagens": respostas, "itens": itens, "chamadas": registros}
