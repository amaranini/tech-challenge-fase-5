"""Fakes em memória dos ports do core — testes sem banco e sem modelo."""

import hashlib
import math
import re
from collections.abc import Mapping, Sequence
from uuid import UUID

from sdr.core.application.ports.agente import EntradaAgente
from sdr.core.application.ports.llm import (
    ChamadaFerramenta,
    DefinicaoFerramenta,
    MensagemLLM,
    RespostaEstruturada,
    RespostaLLM,
)
from sdr.core.application.ports.repositorios import ResumoLead
from sdr.core.domain.agente import RespostaAgente
from sdr.core.domain.catalogo import (
    ConsultaCatalogo,
    ConsultaInvalidaError,
    ItemCatalogo,
    ResultadoCatalogo,
)
from sdr.core.domain.conversa import Canal, Conversa, Lead, Mensagem
from sdr.core.domain.eventos import EventoLead

DIMENSAO_FAKE = 64


def cosseno(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


class EmbeddingFake:
    """Bag-of-words com hashing: textos com palavras em comum ficam próximos."""

    def __init__(self) -> None:
        self.consultas: list[str] = []

    @property
    def dimensao(self) -> int:
        return DIMENSAO_FAKE

    def _vetor(self, texto: str) -> list[float]:
        v = [0.0] * DIMENSAO_FAKE
        for palavra in re.findall(r"\w+", texto.lower()):
            v[int(hashlib.md5(palavra.encode()).hexdigest(), 16) % DIMENSAO_FAKE] += 1
        norma = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / norma for x in v]

    async def gerar_documentos(self, textos: Sequence[str]) -> list[list[float]]:
        return [self._vetor(t) for t in textos]

    async def gerar_consulta(self, texto: str) -> list[float]:
        self.consultas.append(texto)
        return self._vetor(texto)


# ---------------------------------------------------------------- conversa / agente


class LLMRoteirizado:
    """LLM fake que devolve respostas pré-definidas, em ordem, e registra as chamadas."""

    def __init__(
        self, *respostas: RespostaLLM, estruturadas: Sequence[Mapping[str, object]] = ()
    ) -> None:
        self._respostas = list(respostas)
        self._estruturadas = list(estruturadas)
        self.chamadas: list[tuple[list[MensagemLLM], list[DefinicaoFerramenta], bool]] = []
        self.chamadas_estruturadas: list[tuple[list[MensagemLLM], Mapping[str, object], str]] = []

    @property
    def modelo(self) -> str:
        return "fake-1"

    async def gerar(
        self,
        mensagens: Sequence[MensagemLLM],
        ferramentas: Sequence[DefinicaoFerramenta] = (),
        forcar_texto: bool = False,
    ) -> RespostaLLM:
        self.chamadas.append((list(mensagens), list(ferramentas), forcar_texto))
        if not self._respostas:
            raise AssertionError("LLM fake sem respostas restantes")
        return self._respostas.pop(0)

    async def gerar_estruturado(
        self, mensagens: Sequence[MensagemLLM], schema: Mapping[str, object], nome: str
    ) -> RespostaEstruturada:
        self.chamadas_estruturadas.append((list(mensagens), schema, nome))
        if not self._estruturadas:
            raise AssertionError("LLM fake sem respostas estruturadas restantes")
        return RespostaEstruturada(self._estruturadas.pop(0), "fake-1", 3, 3)


def texto(conteudo: str, tokens: int = 10) -> RespostaLLM:
    return RespostaLLM(
        conteudo=conteudo, modelo="fake-1", tokens_entrada=tokens, tokens_saida=tokens
    )


def chamar(nome: str, id_chamada: str = "c1", **argumentos: object) -> RespostaLLM:
    return RespostaLLM(
        conteudo="",
        chamadas=(ChamadaFerramenta(id_chamada, nome, argumentos),),
        modelo="fake-1",
        tokens_entrada=5,
        tokens_saida=5,
    )


class CatalogoFake:
    def __init__(self, *itens: ItemCatalogo) -> None:
        self.itens = {i.id: i for i in itens}
        self.consultas: list[ConsultaCatalogo] = []

    async def buscar(self, consulta: ConsultaCatalogo) -> list[ResultadoCatalogo]:
        self.consultas.append(consulta)
        if consulta.filtros.get("invalido"):
            raise ConsultaInvalidaError("filtro 'invalido' não existe")
        itens = list(self.itens.values())[: consulta.limite]
        return [ResultadoCatalogo(i, 0.9) for i in itens]

    async def obter(self, ids: Sequence[str]) -> list[ItemCatalogo]:
        return [self.itens[i] for i in ids if i in self.itens]


def item(id_: str, titulo: str = "Item") -> ItemCatalogo:
    return ItemCatalogo(id=id_, titulo=titulo, resumo=f"resumo de {id_}", atributos={"preco": 1})


class LeadRepositoryFake:
    def __init__(self) -> None:
        self.leads: dict[UUID, Lead] = {}

    async def obter_por_remetente(self, canal: Canal, remetente_id: str) -> Lead | None:
        return next(
            (
                ld
                for ld in self.leads.values()
                if ld.canal is canal and ld.remetente_id == remetente_id
            ),
            None,
        )

    async def salvar(self, lead: Lead) -> None:
        self.leads[lead.id] = lead

    async def listar(self, canal: Canal | None, limite: int) -> list[ResumoLead]:
        return [ResumoLead(ld, 0, None) for ld in self.leads.values() if canal in (None, ld.canal)]


class ConversaRepositoryFake:
    def __init__(self) -> None:
        self.conversas: dict[UUID, Conversa] = {}
        self.mensagens: list[Mensagem] = []

    async def obter_aberta(self, lead_id: UUID) -> Conversa | None:
        return next((c for c in self.conversas.values() if c.lead_id == lead_id), None)

    async def salvar(self, conversa: Conversa) -> None:
        self.conversas[conversa.id] = conversa

    async def adicionar_mensagem(self, mensagem: Mensagem) -> None:
        self.mensagens.append(mensagem)

    async def ultimas_mensagens(self, conversa_id: UUID, limite: int) -> list[Mensagem]:
        return [m for m in self.mensagens if m.conversa_id == conversa_id][-limite:]


class AgenteRoteirizado:
    """Agente fake: devolve respostas pré-definidas e guarda as entradas recebidas."""

    def __init__(self, *respostas: RespostaAgente | Exception) -> None:
        self._respostas = list(respostas)
        self.entradas: list[EntradaAgente] = []

    async def responder(self, entrada: EntradaAgente) -> RespostaAgente:
        self.entradas.append(entrada)
        resposta = self._respostas.pop(0)
        if isinstance(resposta, Exception):
            raise resposta
        return resposta


class LeadEventoRepositoryFake:
    def __init__(self) -> None:
        self.eventos: list[EventoLead] = []

    async def registrar(self, eventos: Sequence[EventoLead]) -> None:
        self.eventos.extend(eventos)

    async def listar(self, lead_id: UUID, limite: int = 200) -> list[EventoLead]:
        return [e for e in self.eventos if e.lead_id == lead_id][:limite]
