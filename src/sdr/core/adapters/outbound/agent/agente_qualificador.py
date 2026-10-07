"""AgenteConversacionalPort com LangGraph: roteador → extração → scoring → especialista.

    START → roteador ─┬─ (sem intenção) ──────────────→ descoberta ⇄ ferramentas → END
                      └─ (intenção X) → extração → scoring → especialista ⇄ ferramentas → END

- O core não conhece as intenções: elas vêm da vertical (IntencaoVertical).
- Os nós só orquestram; as REGRAS (troca de intenção, merge da ficha, faltantes, eventos)
  são do domínio (`Qualificacao`) e o scoring/critério é da vertical.
- Cada nó usa seu próprio LLMPort (modelo configurável por nó).
- Memória vem do banco (histórico na EntradaAgente); o grafo não persiste nada.
"""

import json
import logging
import operator
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
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
from sdr.core.domain.conversa import Mensagem, Papel, agora
from sdr.core.domain.eventos import EventoLead
from sdr.core.domain.qualificacao import (
    INTENCAO_INDEFINIDA,
    LIMIAR_CONFIANCA_PADRAO,
    IntencaoVertical,
    Qualificacao,
    RegrasQualificacao,
    Score,
    campos_faltantes,
)

logger = logging.getLogger(__name__)

FUSO_PADRAO = ZoneInfo("America/Sao_Paulo")
PASTA_PROMPTS = Path(__file__).resolve().parent / "prompts"
PROMPT_ROTEADOR = (PASTA_PROMPTS / "roteador_v1.md").read_text(encoding="utf-8")
PROMPT_EXTRACAO = (PASTA_PROMPTS / "extracao_v2.md").read_text(encoding="utf-8")
AVISO_TROCA_INTENCAO = (
    "\nATENÇÃO: nesta mensagem o lead mudou de intenção ({de} → {para}). Tudo o que ele "
    "disse antes desta mensagem foi para {de}; NÃO use esses valores nesta ficha (campos "
    "equivalentes já foram herdados). Extraia só o que ele disse agora para {para}.\n"
)


@dataclass(frozen=True)
class LLMsPorNo:
    roteador: LLMPort
    extracao: LLMPort
    agente: LLMPort  # especialistas e descoberta


@dataclass(frozen=True)
class ConfigQualificacao:
    intencoes: Sequence[IntencaoVertical]
    regras: RegrasQualificacao
    prompt_descoberta: str
    limiar_confianca: float = LIMIAR_CONFIANCA_PADRAO
    janela_extracao: int = 6  # mensagens recentes lidas por roteador/extração


class EstadoGrafo(TypedDict):
    # Estado de qualificação (visível)
    lead_id: str
    intencao_atual: str | None
    ficha: dict[str, object]
    campos_faltantes: list[str]
    score: Score | None
    historico: list[Mensagem]
    # Internos do turno
    texto: str
    instrucao_adicional: str | None
    qualificacao: Qualificacao
    qualificacao_inicial: Qualificacao
    roteamento: dict[str, object]
    eventos: Annotated[list[EventoLead], operator.add]
    mensagens: Annotated[list[MensagemLLM], operator.add]
    itens: Annotated[list[ItemCatalogo], operator.add]
    chamadas: Annotated[list[ChamadaFerramentaRegistrada], operator.add]
    tokens_entrada: Annotated[int, operator.add]
    tokens_saida: Annotated[int, operator.add]
    passos: int
    modelo: str | None
    no_resposta: str


def _transcricao(historico: Sequence[Mensagem], texto: str, janela: int) -> str:
    linhas = [
        f"{'Lead' if m.papel is Papel.LEAD else 'Atendente'}: {m.texto}"
        for m in list(historico)[-janela:]
    ]
    linhas.append(f"Lead: {texto}")
    return "Conversa recente:\n" + "\n".join(linhas)


def _nota_itens_citados(mensagem: Mensagem) -> str | None:
    citados = mensagem.metadados.get("itens_citados")
    if not isinstance(citados, list) or not citados:
        return None
    linhas = [
        f"- {c.get('id')}: {c.get('resumo', c.get('titulo', ''))}"
        for c in citados
        if isinstance(c, dict)
    ]
    return "[Contexto interno] Itens apresentados na sua mensagem anterior:\n" + "\n".join(linhas)


def _descricao_campo(intencao: IntencaoVertical, campo: str) -> str:
    propriedades = intencao.schema.schema_extracao().get("properties", {})
    if isinstance(propriedades, Mapping):
        definicao = propriedades.get(campo, {})
        if isinstance(definicao, Mapping) and definicao.get("description"):
            return f"{campo} ({definicao['description']})"
    return campo


class AgenteQualificador:
    def __init__(
        self,
        llms: LLMsPorNo,
        persona: Persona,
        qualificacao: ConfigQualificacao,
        *,
        ferramentas: Sequence[Ferramenta],
        max_passos: int = 4,
        fuso: ZoneInfo = FUSO_PADRAO,
    ) -> None:
        self._llms = llms
        self._persona = persona
        self._config = qualificacao
        self._intencoes = {i.nome: i for i in qualificacao.intencoes}
        if INTENCAO_INDEFINIDA in self._intencoes:
            raise ValueError(f"'{INTENCAO_INDEFINIDA}' é reservada ao core")
        self._ferramentas = {f.definicao.nome: f for f in ferramentas}
        self._max_passos = max_passos
        self._fuso = fuso
        self._grafo = self._construir_grafo()

    # ------------------------------------------------------------------ grafo
    def _construir_grafo(
        self,
    ) -> CompiledStateGraph[EstadoGrafo, None, EstadoGrafo, EstadoGrafo]:
        grafo = StateGraph(EstadoGrafo)
        # Os stubs do LangGraph não reconhecem TypedDict no Protocol genérico de add_node.
        grafo.add_node("roteador", self._no_roteador)  # type: ignore[call-overload]
        grafo.add_node("extracao", self._no_extracao)  # type: ignore[call-overload]
        grafo.add_node("scoring", self._no_scoring)  # type: ignore[call-overload]
        grafo.add_node("especialista", self._no_especialista)  # type: ignore[call-overload]
        grafo.add_node("descoberta", self._no_descoberta)  # type: ignore[call-overload]
        grafo.add_node("ferramentas", self._no_ferramentas)  # type: ignore[call-overload]

        grafo.add_edge(START, "roteador")
        grafo.add_conditional_edges(
            "roteador",
            lambda e: "extracao" if e["intencao_atual"] else "descoberta",
            {"extracao": "extracao", "descoberta": "descoberta"},
        )
        grafo.add_edge("extracao", "scoring")
        grafo.add_edge("scoring", "especialista")
        for no in ("especialista", "descoberta"):
            grafo.add_conditional_edges(
                no, self._apos_resposta, {"ferramentas": "ferramentas", END: END}
            )
        grafo.add_conditional_edges(
            "ferramentas",
            lambda e: e["no_resposta"],
            {"especialista": "especialista", "descoberta": "descoberta"},
        )
        return grafo.compile()

    async def responder(self, entrada: EntradaAgente) -> RespostaAgente:
        q = entrada.lead.qualificacao
        inicial: EstadoGrafo = {
            "lead_id": str(entrada.lead.id),
            "intencao_atual": q.intencao_atual,
            "ficha": dict(q.ficha),
            "campos_faltantes": [],
            "score": q.score,
            "historico": list(entrada.historico),
            "texto": entrada.texto,
            "instrucao_adicional": entrada.instrucao_adicional,
            "qualificacao": q,
            "qualificacao_inicial": q,
            "roteamento": {},
            "eventos": [],
            "mensagens": [],
            "itens": [],
            "chamadas": [],
            "tokens_entrada": 0,
            "tokens_saida": 0,
            "passos": 0,
            "modelo": None,
            "no_resposta": "descoberta",
        }
        final = cast(
            EstadoGrafo,
            await self._grafo.ainvoke(
                inicial, config={"recursion_limit": 2 * self._max_passos + 10}
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
            qualificacao=final["qualificacao"],
            eventos=tuple(final["eventos"]),
            campos_faltantes=tuple(final["campos_faltantes"]),
            metadados={"roteamento": final["roteamento"], "no_resposta": final["no_resposta"]},
        )

    # ------------------------------------------------------------------ roteador
    async def _no_roteador(self, estado: EstadoGrafo) -> dict[str, object]:
        nomes = [*self._intencoes, INTENCAO_INDEFINIDA]
        descricoes = "\n".join(f"- {i.nome}: {i.descricao}" for i in self._intencoes.values())
        sistema = PROMPT_ROTEADOR.format(
            intencoes=descricoes, intencao_atual=estado["intencao_atual"] or "nenhuma"
        )
        schema = {
            "type": "object",
            "properties": {
                "intencao": {"type": "string", "enum": nomes},
                "confianca": {"type": "number", "minimum": 0, "maximum": 1},
            },
            "required": ["intencao", "confianca"],
            "additionalProperties": False,
        }
        transcricao = _transcricao(
            estado["historico"], estado["texto"], self._config.janela_extracao
        )
        resposta = await self._llms.roteador.gerar_estruturado(
            [MensagemLLM(PapelLLM.SISTEMA, sistema), MensagemLLM(PapelLLM.USUARIO, transcricao)],
            schema,
            "classificacao_intencao",
        )
        classificada = str(resposta.dados.get("intencao", INTENCAO_INDEFINIDA))
        if classificada not in nomes:
            classificada = INTENCAO_INDEFINIDA
        try:
            confianca = min(1.0, max(0.0, float(resposta.dados.get("confianca", 0))))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            confianca = 0.0

        destino = self._intencoes.get(classificada)
        q, eventos = estado["qualificacao"].aplicar_intencao(
            classificada,
            confianca,
            agora(),
            campos_destino=destino.schema.campos if destino else (),
            limiar=self._config.limiar_confianca,
        )
        return {
            "qualificacao": q,
            "intencao_atual": q.intencao_atual,
            "ficha": dict(q.ficha),
            "eventos": eventos,
            "roteamento": {
                "classificada": classificada,
                "confianca": confianca,
                "modelo": resposta.modelo,
            },
            "tokens_entrada": resposta.tokens_entrada,
            "tokens_saida": resposta.tokens_saida,
        }

    # ------------------------------------------------------------------ extração
    async def _no_extracao(self, estado: EstadoGrafo) -> dict[str, object]:
        q = estado["qualificacao"]
        intencao = self._intencoes[cast(str, q.intencao_atual)]
        campos = list(intencao.schema.campos)
        lista_de_campos = {"type": "array", "items": {"type": "string", "enum": campos}}
        schema = {
            "type": "object",
            "properties": {
                "campos": dict(intencao.schema.schema_extracao()),
                "campos_corrigidos": lista_de_campos,
                "campos_removidos": lista_de_campos,
            },
            "required": ["campos", "campos_corrigidos", "campos_removidos"],
            "additionalProperties": False,
        }
        anterior = estado["qualificacao_inicial"].intencao_atual
        trocou = anterior is not None and anterior != intencao.nome
        sistema = PROMPT_EXTRACAO.format(
            intencao=intencao.nome,
            ficha=json.dumps(dict(q.ficha), ensure_ascii=False, default=str),
            aviso_troca=(
                AVISO_TROCA_INTENCAO.format(de=anterior, para=intencao.nome) if trocou else ""
            ),
        )
        transcricao = _transcricao(
            estado["historico"], estado["texto"], self._config.janela_extracao
        )
        resposta = await self._llms.extracao.gerar_estruturado(
            [MensagemLLM(PapelLLM.SISTEMA, sistema), MensagemLLM(PapelLLM.USUARIO, transcricao)],
            schema,
            f"ficha_{intencao.nome}",
        )
        brutos = resposta.dados.get("campos")
        extraido = {
            k: v
            for k, v in (brutos.items() if isinstance(brutos, Mapping) else [])
            if v is not None
        }
        validos = intencao.schema.validar(extraido)
        if descartados := set(extraido) - set(validos):
            logger.info("Extração descartou campos inválidos: %s", sorted(descartados))

        def _lista(chave: str) -> list[str]:
            valor = resposta.dados.get(chave)
            return [c for c in valor if c in campos] if isinstance(valor, list) else []

        q, eventos = q.aplicar_extracao(
            validos,
            agora(),
            corrigidos=_lista("campos_corrigidos"),
            removidos=_lista("campos_removidos"),
        )
        return {
            "qualificacao": q,
            "ficha": dict(q.ficha),
            "eventos": eventos,
            "tokens_entrada": resposta.tokens_entrada,
            "tokens_saida": resposta.tokens_saida,
        }

    # ------------------------------------------------------------------ scoring
    async def _no_scoring(self, estado: EstadoGrafo) -> dict[str, object]:
        q = estado["qualificacao"]
        nome = cast(str, q.intencao_atual)
        intencao = self._intencoes[nome]
        regras = self._config.regras

        ficha_antes = estado["qualificacao_inicial"].fichas.get(nome, {})
        qualificado_antes = regras.qualificado(nome, ficha_antes, regras.pontuar(nome, ficha_antes))
        score = regras.pontuar(nome, q.ficha)
        qualificado_agora = regras.qualificado(nome, q.ficha, score)

        q, eventos = q.aplicar_score(
            score,
            qualificado_agora,
            qualificado_antes,
            intencao.proxima_acao_ao_qualificar,
            agora(),
        )
        return {
            "qualificacao": q,
            "score": score,
            "campos_faltantes": campos_faltantes(q.ficha, intencao.prioridade_campos),
            "eventos": eventos,
        }

    # ------------------------------------------------------------------ resposta
    async def _no_especialista(self, estado: EstadoGrafo) -> dict[str, object]:
        novas: list[MensagemLLM] = []
        if not estado["mensagens"]:
            intencao = self._intencoes[cast(str, estado["intencao_atual"])]
            novas = self._contexto(
                estado,
                intencao.prompt_especialista,
                self._bloco_qualificacao(estado, intencao),
            )
        return await self._chamar_agente(estado, novas, "especialista")

    async def _no_descoberta(self, estado: EstadoGrafo) -> dict[str, object]:
        novas: list[MensagemLLM] = []
        if not estado["mensagens"]:
            novas = self._contexto(estado, self._config.prompt_descoberta, None)
        return await self._chamar_agente(estado, novas, "descoberta")

    async def _chamar_agente(
        self, estado: EstadoGrafo, novas: list[MensagemLLM], no: str
    ) -> dict[str, object]:
        esgotou = estado["passos"] >= self._max_passos
        resposta = await self._llms.agente.gerar(
            [*estado["mensagens"], *novas],
            [f.definicao for f in self._ferramentas.values()],
            forcar_texto=esgotou,
        )
        chamadas = () if esgotou else resposta.chamadas
        return {
            "mensagens": [*novas, MensagemLLM(PapelLLM.ASSISTENTE, resposta.conteudo, chamadas)],
            "tokens_entrada": resposta.tokens_entrada,
            "tokens_saida": resposta.tokens_saida,
            "passos": estado["passos"] + 1,
            "modelo": resposta.modelo or estado["modelo"],
            "no_resposta": no,
        }

    @staticmethod
    def _apos_resposta(estado: EstadoGrafo) -> str:
        return "ferramentas" if estado["mensagens"][-1].chamadas else END

    def _contexto(
        self, estado: EstadoGrafo, prompt_no: str, bloco_estado: str | None
    ) -> list[MensagemLLM]:
        momento = datetime.now(self._fuso)
        mensagens = [
            MensagemLLM(PapelLLM.SISTEMA, f"{self._persona.prompt_sistema}\n\n{prompt_no}"),
            MensagemLLM(
                PapelLLM.SISTEMA,
                f"Data e hora atuais: {momento:%d/%m/%Y %H:%M} ({self._fuso.key}).",
            ),
        ]
        for anterior in estado["historico"]:
            if anterior.papel is Papel.LEAD:
                mensagens.append(MensagemLLM(PapelLLM.USUARIO, anterior.texto))
                continue
            mensagens.append(MensagemLLM(PapelLLM.ASSISTENTE, anterior.texto))
            if nota := _nota_itens_citados(anterior):
                mensagens.append(MensagemLLM(PapelLLM.SISTEMA, nota))
        if bloco_estado:
            mensagens.append(MensagemLLM(PapelLLM.SISTEMA, bloco_estado))
        mensagens.append(MensagemLLM(PapelLLM.USUARIO, estado["texto"]))
        if estado["instrucao_adicional"]:
            mensagens.append(MensagemLLM(PapelLLM.SISTEMA, estado["instrucao_adicional"]))
        return mensagens

    @staticmethod
    def _bloco_qualificacao(estado: EstadoGrafo, intencao: IntencaoVertical) -> str:
        q = estado["qualificacao"]
        faltantes = estado["campos_faltantes"]
        linhas = [
            "[Estado da qualificação — uso interno, nunca mostre isto ao lead]",
            f"Intenção: {intencao.nome}",
            f"Ficha atual: {json.dumps(dict(q.ficha), ensure_ascii=False, default=str)}",
        ]
        if q.score:
            linhas.append(f"Score: {q.score.pontos} ({q.score.classificacao.value})")
        pode_sugerir = bool(intencao.campos_para_sugerir) and not campos_faltantes(
            q.ficha, intencao.campos_para_sugerir
        )
        if pode_sugerir:
            linhas.append(
                "Já há dados suficientes para sugerir opções do catálogo: se você ainda não "
                "apresentou opções que atendem à ficha atual, chame a ferramenta de busca "
                "AGORA, antes de responder, e apresente até 3. Mesmo mostrando opções, termine "
                "com UMA única pergunta."
            )
        if q.proxima_acao:
            # Qualificado: o objetivo vira a próxima ação; não se pergunta mais nada da ficha.
            linhas.append(
                f"Lead QUALIFICADO. Próxima ação: {q.proxima_acao}. Sua ÚNICA pergunta nesta "
                "mensagem deve conduzir a essa ação, sem combinar data ou horário (isso é "
                "feito depois). Não pergunte outros dados de qualificação."
            )
        elif faltantes:
            linhas += [
                f"Próximo dado a descobrir: {_descricao_campo(intencao, faltantes[0])}.",
                "Faça NO MÁXIMO UMA pergunta, sobre esse dado, de forma natural e aproveitando "
                "o que o lead já disse; se o lead perguntou algo, responda isso primeiro. "
                "Nunca pareça um formulário.",
            ]
            if len(faltantes) > 1:
                linhas.append(
                    f"Outros dados faltantes (não pergunte agora): {', '.join(faltantes[1:])}"
                )
        else:
            linhas.append("Ficha completa: não pergunte mais dados de qualificação.")
        return "\n".join(linhas)

    # ------------------------------------------------------------------ ferramentas
    async def _no_ferramentas(self, estado: EstadoGrafo) -> dict[str, object]:
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
