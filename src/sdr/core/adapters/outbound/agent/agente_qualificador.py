"""AgenteConversacionalPort com LangGraph: roteador → extração → scoring → especialista.

    START → gate (estado de atendimento)
      ├─ ATENDIMENTO_HUMANO ──────────→ silêncio (a IA não responde) → END
      ├─ AGUARDANDO_HUMANO ───────────→ espera ─────────────────┐
      ├─ CONFIRMANDO_HANDOFF ─────────→ confirmacao_handoff ────┤→ responder_atendimento → END
      ├─ CONFIRMANDO_RETORNO_IA ──────→ confirmacao_retorno ────┤
      └─ ATENDIMENTO_IA → roteador ─┬─ (precisa de humano) → solicitar_handoff ┘
                                    │
    roteador ─┬─ (sem intenção) ──────────────→ descoberta ⇄ ferramentas → END
                      └─ (intenção X) → extração → scoring ─┬→ especialista ⇄ ferramentas → END
                                                            └→ agenda → responder_agenda
                                                                        ⇄ ferramentas → END

- atendimento: o grafo só DECIDE a ação (pedir handoff, confirmar, voltar para a IA...) e
  prevê o novo estado com o domínio para redigir a resposta; quem aplica (com
  compare-and-set) é o turno, que descarta a resposta se o estado mudou no meio.
- agenda: lead qualificado numa intenção com `TipoAgendamento` (ou negociação em curso). O
  LLM só INTERPRETA a fala (ação + preferência estruturada) e depois REDIGE a resposta; os
  horários reais, a confirmação e a reserva são do domínio/`ConduzirAgendamento`. Se o lead
  não quer agendar agora, segue para o especialista sem insistir.

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
from uuid import UUID
from zoneinfo import ZoneInfo

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from sdr.core.adapters.outbound.agent.agenda import (
    bloco_agenda,
    ler_interpretacao,
    prompt_interpretacao,
    schema_interpretacao,
)
from sdr.core.adapters.outbound.agent.atendimento import (
    SCHEMA_ESPERA,
    SCHEMA_RESPOSTA,
    SEM_HANDOFF,
    SINAIS_HUMANO,
    SITUACAO_ESPERA,
    SITUACAO_HANDOFF,
    SITUACAO_RETORNO,
    bloco_atendimento,
    ler_confirmacao,
    ler_sinal_humano,
)
from sdr.core.application.ports.agente import EntradaAgente
from sdr.core.application.ports.ferramenta import Ferramenta, ResultadoFerramenta
from sdr.core.application.ports.llm import LLMPort, MensagemLLM, PapelLLM
from sdr.core.application.ports.relogio import RelogioPort
from sdr.core.application.use_cases.conduzir_agendamento import (
    ConduzirAgendamento,
    EntradaAgenda,
    ResultadoAgenda,
)
from sdr.core.domain.agenda import NegociacaoAgenda, TipoAgendamento, TipoDecisao
from sdr.core.domain.agente import (
    ChamadaFerramentaRegistrada,
    Persona,
    RespostaAgente,
    unicos_por_id,
)
from sdr.core.domain.atendimento import (
    AcaoAtendimento,
    Atendimento,
    EstadoAtendimento,
    HorarioAtendimento,
    MotivoHandoff,
    TipoAcaoAtendimento,
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
PROMPT_ROTEADOR = (PASTA_PROMPTS / "roteador_v2.md").read_text(encoding="utf-8")
PROMPT_CLASSIFICACAO_ATENDIMENTO = (PASTA_PROMPTS / "atendimento_classificacao_v1.md").read_text(
    encoding="utf-8"
)
PROMPT_ATENDIMENTO = (PASTA_PROMPTS / "atendimento_v1.md").read_text(encoding="utf-8")
PROMPT_LIMITES = (PASTA_PROMPTS / "limites_v1.md").read_text(encoding="utf-8")
PROMPT_EXTRACAO = (PASTA_PROMPTS / "extracao_v2.md").read_text(encoding="utf-8")
PROMPT_INTERPRETACAO_AGENDA = (PASTA_PROMPTS / "agenda_interpretacao_v1.md").read_text(
    encoding="utf-8"
)
PROMPT_AGENDAMENTO = (PASTA_PROMPTS / "agendamento_v1.md").read_text(encoding="utf-8")
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
    # Agenda
    negociacao: NegociacaoAgenda
    agenda: ResultadoAgenda | None
    interpretacao_agenda: dict[str, object]
    # Atendimento (IA × humano)
    atendimento: Atendimento
    sinal_humano: MotivoHandoff | None
    quer_agendar: bool
    fora_do_alcance: bool
    acao_atendimento: AcaoAtendimento | None
    atendimento_previsto: Atendimento | None
    silenciar: bool


AUTORES = {Papel.LEAD: "Lead", Papel.ASSISTENTE: "Atendente", Papel.RESPONSAVEL: "Equipe"}
HORARIO_PADRAO = HorarioAtendimento.de_texto("seg-sex", "09:00-18:00", "America/Sao_Paulo")


def _transcricao(historico: Sequence[Mensagem], texto: str, janela: int) -> str:
    linhas = [f"{AUTORES[m.papel]}: {m.texto}" for m in list(historico)[-janela:]]
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


def sem_repeticao(texto: str) -> str:
    """Colapsa a resposta que veio duplicada numa única geração (mesmo conteúdo duas vezes
    seguidas — já visto com o modelo de agente)."""
    texto = texto.strip()
    linhas = [" ".join(linha.split()) for linha in texto.splitlines() if linha.strip()]
    metade = len(linhas) // 2
    if metade and len(linhas) % 2 == 0 and linhas[:metade] == linhas[metade:]:
        alvo, vistas = linhas[metade - 1], 0
        for i, linha in enumerate(texto.splitlines()):
            if " ".join(linha.split()) == alvo:
                vistas += 1
                if vistas == 1:
                    return "\n".join(texto.splitlines()[: i + 1]).strip()
    return texto


def _itens_citados(historico: Sequence[Mensagem], limite: int = 5) -> tuple[str, ...]:
    """Ids dos itens do catálogo citados na conversa, do mais recente ao mais antigo."""
    vistos: dict[str, None] = {}
    for mensagem in reversed(historico):
        citados = mensagem.metadados.get("itens_citados")
        for citado in citados if isinstance(citados, list) else []:
            if isinstance(citado, dict) and citado.get("id"):
                vistos.setdefault(str(citado["id"]), None)
    return tuple(vistos)[:limite]


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
        agenda: ConduzirAgendamento | None = None,
        relogio: RelogioPort | None = None,
        horario: HorarioAtendimento = HORARIO_PADRAO,
    ) -> None:
        self._llms = llms
        self._horario = horario
        self._agenda = agenda
        self._relogio = relogio
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
        grafo.add_node("agenda", self._no_agenda)  # type: ignore[call-overload]
        grafo.add_node("responder_agenda", self._no_responder_agenda)  # type: ignore[call-overload]
        grafo.add_node("gate", self._no_gate)  # type: ignore[call-overload]
        grafo.add_node("silencio", self._no_silencio)  # type: ignore[call-overload]
        grafo.add_node("espera", self._no_espera)  # type: ignore[call-overload]
        grafo.add_node("confirmacao_handoff", self._no_confirmacao_handoff)  # type: ignore[call-overload]
        grafo.add_node("confirmacao_retorno", self._no_confirmacao_retorno)  # type: ignore[call-overload]
        grafo.add_node("solicitar_handoff", self._no_solicitar_handoff)  # type: ignore[call-overload]
        grafo.add_node("responder_atendimento", self._no_responder_atendimento)  # type: ignore[call-overload]

        grafo.add_edge(START, "gate")
        por_estado = {
            EstadoAtendimento.ATENDIMENTO_IA: "roteador",
            EstadoAtendimento.ATENDIMENTO_HUMANO: "silencio",
            EstadoAtendimento.AGUARDANDO_HUMANO: "espera",
            EstadoAtendimento.CONFIRMANDO_HANDOFF: "confirmacao_handoff",
            EstadoAtendimento.CONFIRMANDO_RETORNO_IA: "confirmacao_retorno",
        }
        grafo.add_conditional_edges(
            "gate", lambda e: por_estado[e["atendimento"].estado], sorted(set(por_estado.values()))
        )
        grafo.add_edge("silencio", END)
        for no in ("espera", "confirmacao_handoff", "confirmacao_retorno", "solicitar_handoff"):
            grafo.add_edge(no, "responder_atendimento")
        grafo.add_conditional_edges(
            "roteador",
            lambda e: (
                "solicitar_handoff"
                if e["sinal_humano"]
                else "extracao"
                if e["intencao_atual"]
                else "descoberta"
            ),
            {
                "solicitar_handoff": "solicitar_handoff",
                "extracao": "extracao",
                "descoberta": "descoberta",
            },
        )
        grafo.add_edge("extracao", "scoring")
        grafo.add_conditional_edges(
            "scoring", self._apos_scoring, {"agenda": "agenda", "especialista": "especialista"}
        )
        grafo.add_conditional_edges(
            "agenda",
            lambda e: (
                "especialista"
                if e["agenda"] is None or e["agenda"].decisao.tipo is TipoDecisao.NAO_INSISTIR
                else "responder_agenda"
            ),
            {"especialista": "especialista", "responder_agenda": "responder_agenda"},
        )
        for no in ("especialista", "descoberta", "responder_agenda", "responder_atendimento"):
            grafo.add_conditional_edges(
                no, self._apos_resposta, {"ferramentas": "ferramentas", END: END}
            )
        grafo.add_conditional_edges(
            "ferramentas",
            lambda e: e["no_resposta"],
            {
                "especialista": "especialista",
                "descoberta": "descoberta",
                "responder_agenda": "responder_agenda",
                "responder_atendimento": "responder_atendimento",
            },
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
            "negociacao": entrada.lead.agenda,
            "agenda": None,
            "interpretacao_agenda": {},
            "atendimento": entrada.lead.atendimento_atual,
            "sinal_humano": None,
            "quer_agendar": False,
            "fora_do_alcance": False,
            "acao_atendimento": None,
            "atendimento_previsto": None,
            "silenciar": False,
        }
        final = cast(
            EstadoGrafo,
            await self._grafo.ainvoke(
                inicial, config={"recursion_limit": 2 * self._max_passos + 10}
            ),
        )
        if final["silenciar"]:
            return RespostaAgente(texto="", silenciar=True, metadados={"no_resposta": "silencio"})
        texto = sem_repeticao(final["mensagens"][-1].conteudo) or self._persona.mensagem_fallback
        metadados: dict[str, object] = {
            "roteamento": final["roteamento"],
            "no_resposta": final["no_resposta"],
        }
        if (acao := final["acao_atendimento"]) is not None:
            previsto = final["atendimento_previsto"]
            metadados["atendimento"] = {
                "acao": acao.tipo.value,
                "confirmado": acao.confirmado,
                "motivo": acao.motivo.value if acao.motivo else None,
                "estado_previsto": previsto.estado.value if previsto else None,
            }
        if (agenda := final["agenda"]) is not None:
            metadados["agenda"] = {
                **final["interpretacao_agenda"],
                "decisao": agenda.decisao.tipo.value,
                "aviso": agenda.decisao.aviso.value if agenda.decisao.aviso else None,
                "opcoes": [s.inicio.isoformat() for s in agenda.decisao.opcoes],
                "proposta": (
                    p.slot.inicio.isoformat()
                    if (p := agenda.decisao.proposta) is not None and p.slot is not None
                    else None
                ),
            }
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
            agenda=final["negociacao"],
            acao_atendimento=final["acao_atendimento"],
            metadados=metadados,
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
                "atendimento_humano": {"type": "string", "enum": SINAIS_HUMANO},
                "quer_agendar": {"type": "boolean"},
                "fora_do_alcance": {"type": "boolean"},
            },
            "required": [
                "intencao",
                "confianca",
                "atendimento_humano",
                "quer_agendar",
                "fora_do_alcance",
            ],
            "additionalProperties": False,
        }
        transcricao = _transcricao(
            estado["historico"], estado["texto"], self._config.janela_extracao
        )
        # Isolada: com a fila e falas da equipe no contexto, o modelo "herdava" um pedido de
        # humano já atendido (visto com LLM real). O sinal vale só para a última fala.
        transcricao += (
            f"\n\nÚltima fala do lead (a ÚNICA que vale para atendimento_humano): "
            f"«{estado['texto']}»"
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
            "sinal_humano": ler_sinal_humano(resposta.dados.get("atendimento_humano", SEM_HANDOFF)),
            "quer_agendar": resposta.dados.get("quer_agendar") is True,
            "fora_do_alcance": resposta.dados.get("fora_do_alcance") is True,
            "roteamento": {
                "classificada": classificada,
                "confianca": confianca,
                "atendimento_humano": resposta.dados.get("atendimento_humano"),
                "quer_agendar": resposta.dados.get("quer_agendar"),
                "fora_do_alcance": resposta.dados.get("fora_do_alcance"),
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

    # ------------------------------------------------------------------ atendimento
    @staticmethod
    async def _no_gate(_: EstadoGrafo) -> dict[str, object]:
        """Só roteia pelo estado de atendimento (aresta condicional), antes do roteador."""
        return {}

    @staticmethod
    async def _no_silencio(_: EstadoGrafo) -> dict[str, object]:
        """Atendimento humano em curso: a IA não responde (o turno só registra o lote)."""
        return {"silenciar": True}

    async def _classificar(
        self, estado: EstadoGrafo, situacao: str, schema: dict[str, object], nome: str
    ) -> tuple[Mapping[str, object], int, int]:
        transcricao = _transcricao(
            estado["historico"], estado["texto"], self._config.janela_extracao
        )
        resposta = await self._llms.extracao.gerar_estruturado(
            [
                MensagemLLM(
                    PapelLLM.SISTEMA, PROMPT_CLASSIFICACAO_ATENDIMENTO.format(situacao=situacao)
                ),
                MensagemLLM(PapelLLM.USUARIO, transcricao),
            ],
            schema,
            nome,
        )
        return resposta.dados, resposta.tokens_entrada, resposta.tokens_saida

    def _decidir(
        self, estado: EstadoGrafo, acao: AcaoAtendimento, tokens: tuple[int, int] = (0, 0)
    ) -> dict[str, object]:
        previsto, _ = acao.aplicar(estado["atendimento"], self._agora())
        return {
            "acao_atendimento": acao,
            "atendimento_previsto": previsto,
            "tokens_entrada": tokens[0],
            "tokens_saida": tokens[1],
        }

    async def _no_solicitar_handoff(self, estado: EstadoGrafo) -> dict[str, object]:
        acao = AcaoAtendimento(TipoAcaoAtendimento.SOLICITAR_HANDOFF, motivo=estado["sinal_humano"])
        return self._decidir(estado, acao)

    async def _no_confirmacao_handoff(self, estado: EstadoGrafo) -> dict[str, object]:
        dados, *tokens = await self._classificar(
            estado, SITUACAO_HANDOFF, SCHEMA_RESPOSTA, "resposta_handoff"
        )
        acao = AcaoAtendimento(
            TipoAcaoAtendimento.RESPONDER_HANDOFF, confirmado=ler_confirmacao(dados)
        )
        return self._decidir(estado, acao, (tokens[0], tokens[1]))

    async def _no_confirmacao_retorno(self, estado: EstadoGrafo) -> dict[str, object]:
        dados, *tokens = await self._classificar(
            estado, SITUACAO_RETORNO, SCHEMA_RESPOSTA, "resposta_retorno"
        )
        acao = AcaoAtendimento(
            TipoAcaoAtendimento.RESPONDER_RETORNO_IA, confirmado=ler_confirmacao(dados)
        )
        return self._decidir(estado, acao, (tokens[0], tokens[1]))

    async def _no_espera(self, estado: EstadoGrafo) -> dict[str, object]:
        dados, *tokens = await self._classificar(
            estado, SITUACAO_ESPERA, SCHEMA_ESPERA, "espera_na_fila"
        )
        tipo = (
            TipoAcaoAtendimento.SOLICITAR_RETORNO_IA
            if dados.get("quer_voltar_para_assistente") is True
            else TipoAcaoAtendimento.MENSAGEM_NA_ESPERA
        )
        return self._decidir(estado, AcaoAtendimento(tipo), (tokens[0], tokens[1]))

    async def _no_responder_atendimento(self, estado: EstadoGrafo) -> dict[str, object]:
        novas: list[MensagemLLM] = []
        if not estado["mensagens"]:
            bloco = bloco_atendimento(
                cast(AcaoAtendimento, estado["acao_atendimento"]),
                estado["atendimento"],
                cast(Atendimento, estado["atendimento_previsto"]),
                self._horario,
                self._agora(),
            )
            novas = self._contexto(estado, PROMPT_ATENDIMENTO, bloco)
        return await self._chamar_agente(estado, novas, "responder_atendimento")

    # ------------------------------------------------------------------ agenda
    def _apos_scoring(self, estado: EstadoGrafo) -> str:
        q = estado["qualificacao"]
        if self._agenda is None or self._agenda.tipo_para(q.intencao_atual) is None:
            return "especialista"
        negociacao = estado["negociacao"]
        em_curso = negociacao.proposta is not None or bool(negociacao.ofertados)
        # Pedido explícito de agendar abre a agenda mesmo antes de qualificar (ex.: o lead
        # aceitou "quer marcar um horário com a equipe?" depois de uma dúvida sem
        # resposta — antes disso o grafo voltava ao roteiro de perguntas e "se perdia").
        quer = q.proxima_acao or em_curso or estado["quer_agendar"]
        return "agenda" if quer else "especialista"

    async def _no_agenda(self, estado: EstadoGrafo) -> dict[str, object]:
        conduzir = cast(ConduzirAgendamento, self._agenda)
        q = estado["qualificacao"]
        intencao = cast(str, q.intencao_atual)
        tipo = cast(TipoAgendamento, conduzir.tipo_para(intencao))
        lead_id = UUID(estado["lead_id"])
        negociacao = estado["negociacao"]
        ativo = await conduzir.agendamento_ativo(lead_id)

        sistema = prompt_interpretacao(
            PROMPT_INTERPRETACAO_AGENDA,
            tipo,
            negociacao,
            ativo,
            self._agora().astimezone(conduzir.fuso),
        )
        transcricao = _transcricao(
            estado["historico"], estado["texto"], self._config.janela_extracao
        )
        resposta = await self._llms.extracao.gerar_estruturado(
            [MensagemLLM(PapelLLM.SISTEMA, sistema), MensagemLLM(PapelLLM.USUARIO, transcricao)],
            schema_interpretacao(tipo),
            "interpretacao_agenda",
        )
        interpretacao = ler_interpretacao(resposta.dados, tipo)
        resultado = await conduzir.executar(
            EntradaAgenda(
                lead_id=lead_id,
                intencao=intencao,
                ficha=q.ficha,
                negociacao=negociacao,
                interpretacao=interpretacao,
                ativo=ativo,
                itens=_itens_citados(estado["historico"]),
            )
        )
        return {
            "agenda": resultado,
            "negociacao": resultado.decisao.negociacao,
            "eventos": list(resultado.eventos),
            "interpretacao_agenda": {
                "acao": interpretacao.acao.value,
                "opcao": interpretacao.opcao,
                "modalidade": interpretacao.modalidade,
            },
            "tokens_entrada": resposta.tokens_entrada,
            "tokens_saida": resposta.tokens_saida,
        }

    async def _no_responder_agenda(self, estado: EstadoGrafo) -> dict[str, object]:
        novas: list[MensagemLLM] = []
        if not estado["mensagens"]:
            novas = self._contexto(estado, PROMPT_AGENDAMENTO, self._bloco_agenda(estado))
        return await self._chamar_agente(estado, novas, "responder_agenda")

    def _bloco_agenda(self, estado: EstadoGrafo) -> str:
        fuso = cast(ConduzirAgendamento, self._agenda).fuso
        hoje = self._agora().astimezone(fuso).date()
        return bloco_agenda(cast(ResultadoAgenda, estado["agenda"]), fuso, hoje)

    def _limites(self, estado: EstadoGrafo) -> str:
        """O que a IA consegue fazer, e o próximo passo REAL para o que ela não consegue."""
        tipo = self._agenda.tipo_para(estado["intencao_atual"]) if self._agenda else None
        acoes = ["transferir a conversa para uma pessoa da equipe, se a pessoa pedir"]
        if tipo is not None:
            acoes.insert(0, f"marcar {tipo.rotulo} (a agenda é sua: você propõe horários reais)")
            proximo = f"marcar {tipo.rotulo}, onde essa dúvida é resolvida"
        else:
            proximo = (
                "entender melhor o que a pessoa procura e, se ela preferir, transferir para "
                "uma pessoa da equipe"
            )
        return PROMPT_LIMITES.format(acoes="; ".join(acoes), proximo_passo=proximo)

    def _agora(self) -> datetime:
        return self._relogio.agora() if self._relogio else datetime.now(self._fuso)

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
        momento = self._agora().astimezone(self._fuso)
        mensagens = [
            MensagemLLM(PapelLLM.SISTEMA, f"{self._persona.prompt_sistema}\n\n{prompt_no}"),
            MensagemLLM(PapelLLM.SISTEMA, self._limites(estado)),
            MensagemLLM(
                PapelLLM.SISTEMA,
                f"Data e hora atuais: {momento:%d/%m/%Y %H:%M} ({self._fuso.key}).",
            ),
        ]
        for anterior in estado["historico"]:
            if anterior.papel is Papel.LEAD:
                mensagens.append(MensagemLLM(PapelLLM.USUARIO, anterior.texto))
                continue
            if anterior.papel is Papel.RESPONSAVEL:
                quem = anterior.metadados.get("responsavel") or "pessoa da equipe"
                mensagens.append(
                    MensagemLLM(
                        PapelLLM.SISTEMA,
                        f"[{quem}, da equipe, escreveu ao lead neste ponto da conversa]: "
                        f"{anterior.texto}",
                    )
                )
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
        if q.proxima_acao and estado["negociacao"].recusou:
            linhas.append(
                f"Lead QUALIFICADO (próxima ação: {q.proxima_acao}), mas disse que não quer "
                "agendar agora: NÃO insista. Ajude no que ele pedir; se ele mudar de ideia, "
                "é só dizer."
            )
        elif q.proxima_acao:
            # Qualificado: o objetivo vira a próxima ação; não se pergunta mais nada da ficha.
            linhas.append(
                f"Lead QUALIFICADO. Próxima ação: {q.proxima_acao}. Sua ÚNICA pergunta nesta "
                "mensagem deve conduzir a essa ação, sem combinar data ou horário (isso é "
                "feito depois). Não pergunte outros dados de qualificação."
            )
        elif estado["fora_do_alcance"]:
            # Com o "próximo dado" no bloco, o modelo emendava a pergunta do roteiro na
            # oferta (visto com LLM real) e o "sim" do lead ficava ambíguo.
            linhas.append(
                "O lead pediu algo que você não tem ou não faz (veja os seus limites): diga "
                "isso com honestidade e sua ÚNICA pergunta é a oferta do próximo passo real. "
                "NÃO pergunte nenhum dado de qualificação nesta mensagem."
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
