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
from sdr.core.application.ports.canal import (
    FalhaEnvioError,
    ResultadoEnvio,
    TemplateIndisponivelError,
)
from sdr.core.application.ports.crm import RegistroCRM
from sdr.core.application.ports.followup import (
    GanchosTemplate,
    MensagemAtiva,
    PedidoMensagemAtiva,
)
from sdr.core.application.ports.llm import (
    ChamadaFerramenta,
    DefinicaoFerramenta,
    MensagemLLM,
    RespostaEstruturada,
    RespostaLLM,
)
from sdr.core.application.ports.relogio import RelogioPort
from sdr.core.application.ports.repositorios import AtualizacaoEntrega, ResumoLead
from sdr.core.application.use_cases.entregar_mensagem import ConfigEntrega, EntregarMensagem
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
from sdr.core.domain.conversa import (
    Canal,
    Conversa,
    Lead,
    Mensagem,
    Papel,
    StatusEntrega,
    StatusMensagem,
    agora,
    avancar_entrega,
    entrega_da_mensagem,
)
from sdr.core.domain.eventos import EventoLead
from sdr.core.domain.followup import FollowUp, StatusFollowUp, TipoFollowUp
from sdr.core.domain.resumo import (
    AfirmacaoChecada,
    FatosResumo,
    FonteAfirmacao,
    RascunhoResumo,
    Resumo,
    TemplateResumo,
)
from sdr.core.domain.template import CategoriaTemplate, TemplateLogico, VariavelTemplate

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
        if anterior is None:
            self.leads[lead.id] = lead
            return
        self.leads[lead.id] = replace(
            lead, atendimento=anterior.atendimento, opt_out_em=anterior.opt_out_em
        )

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

    async def registrar_opt_out(self, lead_id: UUID, momento: datetime) -> None:
        lead = self.leads[lead_id]
        if lead.opt_out_em is None:
            self.leads[lead_id] = replace(lead, opt_out_em=momento)

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

    async def adicionar_recebida(self, mensagem: Mensagem) -> bool:
        if mensagem.id_externo and await self.mensagem_externa_existe(mensagem.id_externo):
            return False
        self.mensagens.append(mensagem)
        return True

    async def mensagem_externa_existe(self, id_externo: str) -> bool:
        return any(m.id_externo == id_externo for m in self.mensagens)

    async def ultima_do_lead(self, conversa_id: UUID) -> datetime | None:
        return max(
            (
                m.criada_em
                for m in self.mensagens
                if m.conversa_id == conversa_id and m.papel is Papel.LEAD
            ),
            default=None,
        )

    async def ultimas_mensagens(
        self, conversa_id: UUID, limite: int, *, incluir_pendentes: bool = False
    ) -> list[Mensagem]:
        fora = {StatusMensagem.PENDENTE, StatusMensagem.NAO_ENVIADA}
        return [
            m
            for m in self.mensagens
            if m.conversa_id == conversa_id and (incluir_pendentes or m.status not in fora)
        ][-limite:]

    def mensagem(self, mensagem_id: UUID) -> Mensagem:
        return next(m for m in self.mensagens if m.id == mensagem_id)

    def trocar(self, mensagem: Mensagem) -> None:
        self.mensagens = [mensagem if m.id == mensagem.id else m for m in self.mensagens]

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
    """Registra o que o core mandou executar. `templates_mapeados`: None = todos."""

    def __init__(self, templates_mapeados: set[str] | None = None, falhar: bool = False) -> None:
        self.enviadas: list[tuple[Lead, Mensagem]] = []
        self.templates: list[tuple[Lead, str, dict[str, str]]] = []
        self._mapeados = templates_mapeados
        self._falhar = falhar
        self._seq = 0

    def _sid(self) -> str:
        self._seq += 1
        return f"SM{self._seq:04d}"

    async def enviar_texto(self, lead: Lead, mensagem: Mensagem) -> ResultadoEnvio:
        if self._falhar:
            raise FalhaEnvioError("provedor fora do ar")
        self.enviadas.append((lead, mensagem))
        return ResultadoEnvio((self._sid(),))

    async def enviar_template(
        self,
        lead: Lead,
        mensagem: Mensagem,
        nome_logico: str,
        variaveis: Mapping[str, str],
    ) -> ResultadoEnvio:
        if self._mapeados is not None and nome_logico not in self._mapeados:
            raise TemplateIndisponivelError(f"{nome_logico} sem mapeamento")
        self.templates.append((lead, nome_logico, dict(variaveis)))
        return ResultadoEnvio((self._sid(),))


class EntregaRepositoryFake:
    """Grava o status de entrega direto nas mensagens do ConversaRepositoryFake."""

    def __init__(self, conversas: ConversaRepositoryFake) -> None:
        self._conversas = conversas
        self.partes: dict[str, tuple[UUID, StatusEntrega]] = {}
        self.erros: dict[UUID, str] = {}

    async def registrar_envio(
        self, mensagem_id: UUID, ids_externos: Sequence[str], momento: datetime
    ) -> None:
        for id_externo in ids_externos:
            self.partes[id_externo] = (mensagem_id, StatusEntrega.ENVIADA)
        m = self._conversas.mensagem(mensagem_id)
        self._conversas.trocar(replace(m, entrega=m.entrega or StatusEntrega.ENVIADA))

    async def marcar_falha(
        self, mensagem_id: UUID, erro: str, momento: datetime, *, nao_enviada: bool
    ) -> None:
        self.erros[mensagem_id] = erro
        m = self._conversas.mensagem(mensagem_id)
        status = StatusMensagem.NAO_ENVIADA if nao_enviada else m.status
        self._conversas.trocar(replace(m, entrega=StatusEntrega.FALHOU, status=status))

    async def atualizar(
        self, id_externo: str, status: StatusEntrega, erro: str | None, momento: datetime
    ) -> AtualizacaoEntrega | None:
        if id_externo not in self.partes:
            return None
        mensagem_id, atual = self.partes[id_externo]
        self.partes[id_externo] = (mensagem_id, avancar_entrega(atual, status))
        m = self._conversas.mensagem(mensagem_id)
        agregado = entrega_da_mensagem(
            [st for mid, st in self.partes.values() if mid == mensagem_id]
        )
        self._conversas.trocar(replace(m, entrega=agregado))
        conversa = self._conversas.conversas[m.conversa_id]
        return AtualizacaoEntrega(conversa.lead_id, mensagem_id, m.entrega, agregado)


class _RelogioReal:
    def agora(self) -> datetime:
        return agora()


def entrega_fake(
    conversas: ConversaRepositoryFake,
    eventos: "LeadEventoRepositoryFake",
    canal: CanalFake,
    relogio: RelogioPort | None = None,
    *,
    janela: timedelta = timedelta(hours=24),
) -> EntregarMensagem:
    return EntregarMensagem(
        conversas,
        EntregaRepositoryFake(conversas),
        eventos,
        {Canal.WEB: canal, Canal.WHATSAPP: canal},
        relogio=relogio or _RelogioReal(),
        config=ConfigEntrega(fuso=FUSO_SP, janela_conversa=janela),
    )


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


# ---------------------------------------------------------------- follow-up


class FollowUpRepositoryFake:
    def __init__(self) -> None:
        self.itens: dict[UUID, FollowUp] = {}

    async def agendar(self, followup: FollowUp) -> None:
        self.itens[followup.id] = followup

    async def cancelar_pendentes(
        self, lead_id: UUID, tipos: Sequence[TipoFollowUp], motivo: str
    ) -> int:
        alvos = [
            f
            for f in self.itens.values()
            if f.lead_id == lead_id and f.status is StatusFollowUp.PENDENTE and f.tipo in tipos
        ]
        for f in alvos:
            self.itens[f.id] = replace(f, status=StatusFollowUp.CANCELADO, motivo=motivo)
        return len(alvos)

    async def reservar_vencidos(
        self, agora: datetime, limite: int, *, travado_ha: float = 600
    ) -> list[FollowUp]:
        vencidos = sorted(
            (
                f
                for f in self.itens.values()
                if f.status is StatusFollowUp.PENDENTE and f.executar_em <= agora
            ),
            key=lambda f: f.executar_em,
        )[:limite]
        for f in vencidos:
            self.itens[f.id] = replace(f, status=StatusFollowUp.PROCESSANDO)
        return vencidos

    async def concluir(self, followup_id: UUID, status: StatusFollowUp, motivo: str | None) -> None:
        self.itens[followup_id] = replace(self.itens[followup_id], status=status, motivo=motivo)

    async def reagendar(self, followup: FollowUp) -> None:
        self.itens[followup.id] = replace(followup, status=StatusFollowUp.PENDENTE)

    async def pendentes(self, lead_id: UUID) -> list[FollowUp]:
        return sorted(
            (
                f
                for f in self.itens.values()
                if f.lead_id == lead_id and f.status is StatusFollowUp.PENDENTE
            ),
            key=lambda f: f.executar_em,
        )

    def do_lead(self, lead_id: UUID, tipo: TipoFollowUp | None = None) -> list[FollowUp]:
        return [f for f in self.itens.values() if f.lead_id == lead_id and tipo in (None, f.tipo)]


class RedatorMensagemAtivaFake:
    def __init__(self, *textos: str) -> None:
        self._textos = list(textos)
        self.pedidos: list[PedidoMensagemAtiva] = []

    async def redigir(self, pedido: PedidoMensagemAtiva) -> MensagemAtiva:
        self.pedidos.append(pedido)
        texto = self._textos.pop(0) if self._textos else "Oi! Tudo bem por aí?"
        return MensagemAtiva(texto, "fake-1", 10, 10)

    async def preencher_ganchos(
        self, pedido: PedidoMensagemAtiva, template: TemplateLogico
    ) -> GanchosTemplate:
        self.pedidos.append(pedido)
        texto = self._textos.pop(0) if self._textos else "Lembrei da sua busca."
        return GanchosTemplate({v.nome: texto for v in template.ganchos}, "fake-1", 5, 5)


def template_teste(nome: str) -> TemplateLogico:
    """Template lógico mínimo: nome do lead (da ficha/contexto) + um gancho (LLM)."""
    return TemplateLogico(
        nome,
        CategoriaTemplate.UTILITY,
        "Oi, {{primeiro_nome}}! {{gancho}} Responda quando puder.",
        (
            VariavelTemplate(
                "primeiro_nome", "nome", padrao="tudo bem", preencher=lambda c: c.primeiro_nome
            ),
            VariavelTemplate("gancho", "frase", padrao="Lembrei de você.", gancho=True),
        ),
    )
