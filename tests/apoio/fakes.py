"""Fakes em memória dos ports do core — testes sem banco e sem modelo."""

import hashlib
import math
import re
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import date, datetime, time, timedelta
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from sdr.core.application.ports.agente import EntradaAgente
from sdr.core.application.ports.crm import RegistroCRM
from sdr.core.application.ports.llm import (
    ChamadaFerramenta,
    DefinicaoFerramenta,
    MensagemLLM,
    RespostaEstruturada,
    RespostaLLM,
)
from sdr.core.application.ports.repositorios import ResumoLead
from sdr.core.domain.agenda import (
    Agendamento,
    PedidoReserva,
    Responsavel,
    Slot,
    SlotIndisponivelError,
    StatusAgendamento,
)
from sdr.core.domain.agente import RespostaAgente
from sdr.core.domain.atendimento import Atendimento, EstadoAtendimento
from sdr.core.domain.catalogo import (
    ConsultaCatalogo,
    ConsultaInvalidaError,
    ItemCatalogo,
    ResultadoCatalogo,
)
from sdr.core.domain.conversa import Canal, Conversa, Lead, Mensagem, StatusMensagem
from sdr.core.domain.eventos import EventoLead
from sdr.core.domain.resumo import (
    AfirmacaoChecada,
    FatosResumo,
    FonteAfirmacao,
    RascunhoResumo,
    Resumo,
    TemplateResumo,
)

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
    """`salvar` não mexe no atendimento (como o repositório SQL): só `salvar_atendimento`."""

    def __init__(self) -> None:
        self.leads: dict[UUID, Lead] = {}

    async def obter(self, lead_id: UUID) -> Lead | None:
        return self.leads.get(lead_id)

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
        anterior = self.leads.get(lead.id)
        atendimento = anterior.atendimento if anterior else lead.atendimento
        self.leads[lead.id] = replace(lead, atendimento=atendimento)

    async def listar(self, canal: Canal | None, limite: int) -> list[ResumoLead]:
        return [ResumoLead(ld, 0, None) for ld in self.leads.values() if canal in (None, ld.canal)]

    async def salvar_atendimento(
        self, atendimento: Atendimento, esperado: EstadoAtendimento
    ) -> bool:
        lead = self.leads.get(atendimento.lead_id)
        if lead is None or lead.atendimento_atual.estado is not esperado:
            return False
        self.leads[lead.id] = replace(lead, atendimento=atendimento)
        return True

    async def listar_por_atendimento(
        self, estados: Sequence[EstadoAtendimento], limite: int = 100
    ) -> list[Lead]:
        na_fila = [ld for ld in self.leads.values() if ld.atendimento_atual.estado in estados]
        return sorted(na_fila, key=lambda ld: ld.atendimento_atual.na_fila_desde or ld.criado_em)[
            :limite
        ]


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

    async def ultimas_mensagens(
        self, conversa_id: UUID, limite: int, *, incluir_pendentes: bool = False
    ) -> list[Mensagem]:
        return [
            m
            for m in self.mensagens
            if m.conversa_id == conversa_id
            and (incluir_pendentes or m.status is not StatusMensagem.PENDENTE)
        ][-limite:]

    async def pendentes(self, conversa_id: UUID) -> list[Mensagem]:
        return [
            m
            for m in self.mensagens
            if m.conversa_id == conversa_id and m.status is StatusMensagem.PENDENTE
        ]

    async def marcar_status(self, ids: Sequence[UUID], status: StatusMensagem) -> None:
        alvo = set(ids)
        self.mensagens = [replace(m, status=status) if m.id in alvo else m for m in self.mensagens]

    async def leads_com_pendentes(self) -> list[UUID]:
        conversas = {m.conversa_id for m in self.mensagens if m.status is StatusMensagem.PENDENTE}
        return [c.lead_id for c in self.conversas.values() if c.id in conversas]

    def textos(self, status: StatusMensagem | None = None) -> list[str]:
        return [m.texto for m in self.mensagens if status is None or m.status is status]


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


class AgendadorFake:
    """Só registra os agendamentos; o teste dispara o turno quando quiser."""

    def __init__(self) -> None:
        self.agendados: list[UUID] = []

    def agendar(self, lead_id: UUID) -> None:
        self.agendados.append(lead_id)


class TravaFake:
    def __init__(self) -> None:
        self.ocupadas: set[UUID] = set()
        self.maximo_simultaneo = 0

    @asynccontextmanager
    async def travar(self, lead_id: UUID) -> AsyncIterator[bool]:
        if lead_id in self.ocupadas:
            yield False
            return
        self.ocupadas.add(lead_id)
        self.maximo_simultaneo = max(self.maximo_simultaneo, len(self.ocupadas))
        try:
            yield True
        finally:
            self.ocupadas.discard(lead_id)


class CanalFake:
    def __init__(self) -> None:
        self.enviadas: list[tuple[Lead, Mensagem]] = []

    async def enviar(self, lead: Lead, mensagem: Mensagem) -> None:
        self.enviadas.append((lead, mensagem))

    async def enviar_template(
        self, lead: Lead, template: str, variaveis: Mapping[str, str]
    ) -> None:
        raise AssertionError("template não esperado")


# ---------------------------------------------------------------- agenda

FUSO_SP = ZoneInfo("America/Sao_Paulo")


class RelogioFake:
    def __init__(self, momento: datetime) -> None:
        self.momento = momento

    def agora(self) -> datetime:
        return self.momento

    def avancar(self, **delta: float) -> None:
        self.momento += timedelta(**delta)


def grade(
    responsaveis: Sequence[Responsavel],
    dias: Sequence[date],
    horas: Sequence[int] = (9, 10, 11, 14, 15, 16, 18, 19),
    fuso: ZoneInfo = FUSO_SP,
) -> list[Slot]:
    return [
        Slot(
            uuid4(),
            r.id,
            datetime.combine(d, time(h), fuso),
            datetime.combine(d, time(h), fuso) + timedelta(hours=1),
        )
        for d in dias
        for r in responsaveis
        for h in horas
    ]


class AgendaFake:
    """AgendaPort em memória, com a mesma semântica do mock em Postgres."""

    def __init__(self, responsaveis: Sequence[Responsavel], slots: Sequence[Slot]) -> None:
        self.responsaveis = {r.id: r for r in responsaveis}
        self.slots = {s.id: s for s in slots}
        self.ocupados: dict[UUID, UUID | None] = {}  # slot → agendamento (None = externo)
        self.agendamentos: dict[UUID, Agendamento] = {}
        self.por_chave: dict[str, UUID] = {}
        self.chamadas: list[str] = []

    def ocupar_por_fora(self, slot_id: UUID) -> None:
        """Outro lead (ou compromisso externo) toma o slot."""
        self.ocupados[slot_id] = None

    async def listar_responsaveis(self) -> list[Responsavel]:
        return list(self.responsaveis.values())

    async def listar_disponibilidade(
        self, responsavel_ids: Sequence[UUID], inicio: datetime, fim: datetime
    ) -> list[Slot]:
        return sorted(
            (
                s
                for s in self.slots.values()
                if s.responsavel_id in responsavel_ids
                and inicio <= s.inicio < fim
                and s.id not in self.ocupados
            ),
            key=lambda s: s.inicio,
        )

    def _ocupar(self, slot_id: UUID, agendamento_id: UUID) -> Slot:
        if slot_id in self.ocupados or slot_id not in self.slots:
            raise SlotIndisponivelError(str(slot_id))
        self.ocupados[slot_id] = agendamento_id
        return self.slots[slot_id]

    async def reservar(self, pedido: PedidoReserva) -> Agendamento:
        self.chamadas.append("reservar")
        if pedido.chave_idempotencia in self.por_chave:
            return self.agendamentos[self.por_chave[pedido.chave_idempotencia]]
        novo_id = uuid4()
        slot = self._ocupar(pedido.slot_id, novo_id)
        agendamento = Agendamento(
            id=novo_id,
            lead_id=pedido.lead_id,
            responsavel=self.responsaveis[slot.responsavel_id],
            slot_id=slot.id,
            inicio=slot.inicio,
            fim=slot.fim,
            tipo=pedido.tipo,
            modalidade=pedido.modalidade,
            status=StatusAgendamento.ATIVO,
            criado_em=slot.inicio,
            itens=pedido.itens,
        )
        self.agendamentos[novo_id] = agendamento
        self.por_chave[pedido.chave_idempotencia] = novo_id
        return agendamento

    async def remarcar(
        self, agendamento_id: UUID, novo_slot_id: UUID, modalidade: str
    ) -> Agendamento:
        self.chamadas.append("remarcar")
        atual = self.agendamentos[agendamento_id]
        if atual.slot_id != novo_slot_id:
            slot = self._ocupar(novo_slot_id, agendamento_id)
            self.ocupados.pop(atual.slot_id, None)
            atual = replace(
                atual,
                slot_id=slot.id,
                inicio=slot.inicio,
                fim=slot.fim,
                responsavel=self.responsaveis[slot.responsavel_id],
            )
        atual = replace(atual, modalidade=modalidade)
        self.agendamentos[agendamento_id] = atual
        return atual

    async def cancelar(self, agendamento_id: UUID) -> Agendamento:
        self.chamadas.append("cancelar")
        atual = self.agendamentos[agendamento_id]
        if atual.status is not StatusAgendamento.CANCELADO:
            self.ocupados.pop(atual.slot_id, None)
            atual = replace(atual, status=StatusAgendamento.CANCELADO)
            self.agendamentos[agendamento_id] = atual
        return atual

    async def listar_agendamentos(
        self,
        *,
        lead_id: UUID | None = None,
        a_partir_de: datetime | None = None,
        status: StatusAgendamento | None = None,
        limite: int = 100,
    ) -> list[Agendamento]:
        return sorted(
            (
                a
                for a in self.agendamentos.values()
                if (lead_id is None or a.lead_id == lead_id)
                and (a_partir_de is None or a.fim > a_partir_de)
                and (status is None or a.status is status)
            ),
            key=lambda a: a.inicio,
        )[:limite]

    async def agendamento_ativo(self, lead_id: UUID, a_partir_de: datetime) -> Agendamento | None:
        ativos = [
            a
            for a in self.agendamentos.values()
            if a.lead_id == lead_id and a.status is StatusAgendamento.ATIVO and a.fim > a_partir_de
        ]
        return min(ativos, key=lambda a: a.inicio) if ativos else None


# ---------------------------------------------------------------- resumo / CRM


class RedatorRoteirizado:
    """Redator fake: devolve seções pré-definidas e guarda os fatos recebidos.

    Checagem: por padrão, cada texto é UMA frase sustentada (recomendação); `checagens`
    sobrescreve por seção."""

    def __init__(
        self,
        *rascunhos: Mapping[str, object],
        checagens: Mapping[str, Sequence[AfirmacaoChecada]] | None = None,
    ) -> None:
        self._rascunhos = list(rascunhos)
        self._checagens = dict(checagens or {})
        self.fatos: list[FatosResumo] = []
        self.checados: list[Mapping[str, str]] = []

    async def checar(
        self, fatos: FatosResumo, textos: Mapping[str, str]
    ) -> Mapping[str, Sequence[AfirmacaoChecada]]:
        self.checados.append(dict(textos))
        return {
            chave: self._checagens.get(
                chave, [AfirmacaoChecada(texto, True, FonteAfirmacao.RECOMENDACAO)]
            )
            for chave, texto in textos.items()
        }

    async def redigir(self, fatos: FatosResumo, template: TemplateResumo) -> RascunhoResumo:
        self.fatos.append(fatos)
        secoes = self._rascunhos.pop(0) if self._rascunhos else {}
        return RascunhoResumo(dict(secoes), "fake-1", 100, 50)


class ResumoRepositoryFake:
    def __init__(self) -> None:
        self.resumos: list[Resumo] = []

    async def salvar(self, resumo: Resumo) -> None:
        if any(r.lead_id == resumo.lead_id and r.versao == resumo.versao for r in self.resumos):
            raise AssertionError("versão duplicada")
        self.resumos.append(resumo)

    async def ultimo(self, lead_id: UUID) -> Resumo | None:
        do_lead = [r for r in self.resumos if r.lead_id == lead_id]
        return max(do_lead, key=lambda r: r.versao, default=None)

    async def obter(self, lead_id: UUID, versao: int) -> Resumo | None:
        return next((r for r in self.resumos if r.lead_id == lead_id and r.versao == versao), None)

    async def versoes(self, lead_id: UUID) -> list[int]:
        return sorted(r.versao for r in self.resumos if r.lead_id == lead_id)


class CRMFake:
    def __init__(self) -> None:
        self.registros: dict[UUID, tuple[Lead, Resumo, Agendamento | None]] = {}
        self.chamadas = 0

    async def registrar(
        self, lead: Lead, resumo: Resumo, agendamento: Agendamento | None
    ) -> RegistroCRM:
        self.chamadas += 1
        criado = lead.id not in self.registros
        self.registros[lead.id] = (lead, resumo, agendamento)
        return RegistroCRM(f"CRM-{lead.id.hex[:4]}", criado, resumo.gerado_em)
