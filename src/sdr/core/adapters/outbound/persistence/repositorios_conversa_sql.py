from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sdr.core.adapters.outbound.persistence.modelos import (
    ConversaModel,
    LeadEventoModel,
    LeadModel,
    MensagemModel,
)
from sdr.core.application.ports.repositorios import ResumoLead
from sdr.core.domain.agenda import NegociacaoAgenda, Operacao, Proposta, Slot
from sdr.core.domain.atendimento import Atendimento, EstadoAtendimento, MotivoHandoff
from sdr.core.domain.conversa import (
    Canal,
    Conversa,
    Lead,
    Mensagem,
    Papel,
    StatusConversa,
    StatusMensagem,
)
from sdr.core.domain.eventos import EventoLead, TipoEvento
from sdr.core.domain.qualificacao import Classificacao, Qualificacao, Score


def _qualificacao(m: LeadModel) -> Qualificacao:
    score = None
    if m.score is not None and m.classificacao is not None:
        score = Score(m.score, Classificacao(m.classificacao), tuple(m.score_motivos or ()))
    return Qualificacao(
        lead_id=m.id,
        intencao_atual=m.intencao_atual,
        fichas={k: dict(v) for k, v in (m.ficha_qualificacao or {}).items()},
        score=score,
        proxima_acao=m.proxima_acao,
        qualificado_em=m.qualificado_em,
    )


def _slot_para_json(slot: Slot) -> dict[str, str]:
    return {
        "id": str(slot.id),
        "responsavel_id": str(slot.responsavel_id),
        "inicio": slot.inicio.isoformat(),
        "fim": slot.fim.isoformat(),
    }


def _slot_de_json(dados: Mapping[str, Any]) -> Slot:
    return Slot(
        id=UUID(dados["id"]),
        responsavel_id=UUID(dados["responsavel_id"]),
        inicio=datetime.fromisoformat(dados["inicio"]),
        fim=datetime.fromisoformat(dados["fim"]),
    )


def negociacao_para_json(negociacao: NegociacaoAgenda) -> dict[str, Any]:
    if negociacao == NegociacaoAgenda():
        return {}
    p = negociacao.proposta
    return {
        "ofertados": [_slot_para_json(s) for s in negociacao.ofertados],
        "proposta": None
        if p is None
        else {
            "operacao": p.operacao.value,
            "slot": _slot_para_json(p.slot) if p.slot else None,
            "modalidade": p.modalidade,
            "agendamento_id": str(p.agendamento_id) if p.agendamento_id else None,
        },
        "recusou": negociacao.recusou,
    }


def negociacao_de_json(dados: Mapping[str, Any] | None) -> NegociacaoAgenda:
    if not dados:
        return NegociacaoAgenda()
    p = dados.get("proposta")
    proposta = (
        None
        if not p
        else Proposta(
            operacao=Operacao(p["operacao"]),
            slot=_slot_de_json(p["slot"]) if p.get("slot") else None,
            modalidade=p.get("modalidade"),
            agendamento_id=UUID(p["agendamento_id"]) if p.get("agendamento_id") else None,
        )
    )
    return NegociacaoAgenda(
        ofertados=tuple(_slot_de_json(s) for s in dados.get("ofertados", [])),
        proposta=proposta,
        recusou=bool(dados.get("recusou", False)),
    )


def _atendimento(m: LeadModel) -> Atendimento:
    dados = m.atendimento or {}
    desde = dados.get("desde")
    motivo = dados.get("motivo")
    return Atendimento(
        lead_id=m.id,
        estado=EstadoAtendimento(m.atendimento_estado),
        desde=datetime.fromisoformat(desde) if desde else None,
        na_fila_desde=m.na_fila_desde,
        motivo=MotivoHandoff(motivo) if motivo else None,
        responsavel=dados.get("responsavel"),
        respostas_ambiguas=int(dados.get("respostas_ambiguas", 0)),
    )


def _atendimento_para_colunas(a: Atendimento) -> dict[str, Any]:
    return {
        "atendimento_estado": a.estado.value,
        "na_fila_desde": a.na_fila_desde,
        "atendimento": {
            "desde": a.desde.isoformat() if a.desde else None,
            "motivo": a.motivo.value if a.motivo else None,
            "responsavel": a.responsavel,
            "respostas_ambiguas": a.respostas_ambiguas,
        },
    }


def _lead(m: LeadModel) -> Lead:
    return Lead(
        id=m.id,
        canal=Canal(m.canal),
        remetente_id=m.remetente_id,
        criado_em=m.criado_em,
        qualificacao=_qualificacao(m),
        nome=m.nome,
        agenda=negociacao_de_json(m.agenda),
        atendimento=_atendimento(m),
        opt_out_em=m.opt_out_em,
    )


def _conversa(m: ConversaModel) -> Conversa:
    return Conversa(
        id=m.id,
        lead_id=m.lead_id,
        canal=Canal(m.canal),
        iniciada_em=m.iniciada_em,
        atualizada_em=m.atualizada_em,
        status=StatusConversa(m.status),
    )


def _mensagem(m: MensagemModel) -> Mensagem:
    return Mensagem(
        id=m.id,
        conversa_id=m.conversa_id,
        papel=Papel(m.papel),
        texto=m.texto,
        criada_em=m.criada_em,
        metadados=dict(m.metadados or {}),
        status=StatusMensagem(m.status),
    )


class LeadRepositorySql:
    def __init__(self, sessoes: async_sessionmaker[AsyncSession]) -> None:
        self._sessoes = sessoes

    async def obter(self, lead_id: UUID) -> Lead | None:
        async with self._sessoes() as sessao:
            modelo = await sessao.get(LeadModel, lead_id)
        return _lead(modelo) if modelo else None

    async def obter_por_remetente(self, canal: Canal, remetente_id: str) -> Lead | None:
        async with self._sessoes() as sessao:
            modelo = await sessao.scalar(
                select(LeadModel).where(
                    LeadModel.canal == canal.value, LeadModel.remetente_id == remetente_id
                )
            )
        return _lead(modelo) if modelo else None

    async def salvar(self, lead: Lead) -> None:
        q = lead.qualificacao
        mutaveis = {
            "nome": lead.nome,
            "ficha_qualificacao": {k: dict(v) for k, v in q.fichas.items()},
            "intencao_atual": q.intencao_atual,
            "score": q.score.pontos if q.score else None,
            "classificacao": q.score.classificacao.value if q.score else None,
            "score_motivos": list(q.score.motivos) if q.score else [],
            "proxima_acao": q.proxima_acao,
            "qualificado_em": q.qualificado_em,
            "agenda": negociacao_para_json(lead.agenda),
        }
        stmt = insert(LeadModel).values(
            id=lead.id,
            canal=lead.canal.value,
            remetente_id=lead.remetente_id,
            criado_em=lead.criado_em,
            **mutaveis,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[LeadModel.id],
            set_={**{c: stmt.excluded[c] for c in mutaveis}, "atualizado_em": func.now()},
        )
        async with self._sessoes.begin() as sessao:
            await sessao.execute(stmt)

    async def listar(self, canal: Canal | None, limite: int) -> list[ResumoLead]:
        ultima = func.max(MensagemModel.criada_em)
        stmt = (
            select(LeadModel, func.count(MensagemModel.id), ultima)
            .outerjoin(ConversaModel, ConversaModel.lead_id == LeadModel.id)
            .outerjoin(MensagemModel, MensagemModel.conversa_id == ConversaModel.id)
            .group_by(LeadModel.id)
            .order_by(func.coalesce(ultima, LeadModel.criado_em).desc())
            .limit(limite)
        )
        if canal is not None:
            stmt = stmt.where(LeadModel.canal == canal.value)
        async with self._sessoes() as sessao:
            linhas = (await sessao.execute(stmt)).all()
        return [ResumoLead(_lead(m), total, ultima_em) for m, total, ultima_em in linhas]

    async def registrar_opt_out(self, lead_id: UUID, momento: datetime) -> None:
        async with self._sessoes.begin() as sessao:
            await sessao.execute(
                update(LeadModel)
                .where(LeadModel.id == lead_id, LeadModel.opt_out_em.is_(None))
                .values(opt_out_em=momento, atualizado_em=func.now())
            )

    async def salvar_atendimento(
        self, atendimento: Atendimento, esperado: EstadoAtendimento
    ) -> bool:
        async with self._sessoes.begin() as sessao:
            resultado = await sessao.execute(
                update(LeadModel)
                .where(
                    LeadModel.id == atendimento.lead_id,
                    LeadModel.atendimento_estado == esperado.value,
                )
                .values(**_atendimento_para_colunas(atendimento), atualizado_em=func.now())
            )
        return bool(resultado.rowcount)  # type: ignore[attr-defined]

    async def listar_por_atendimento(
        self, estados: Sequence[EstadoAtendimento], limite: int = 100
    ) -> list[Lead]:
        async with self._sessoes() as sessao:
            modelos = await sessao.scalars(
                select(LeadModel)
                .where(LeadModel.atendimento_estado.in_([e.value for e in estados]))
                .order_by(LeadModel.na_fila_desde.asc().nulls_last(), LeadModel.atualizado_em)
                .limit(limite)
            )
            return [_lead(m) for m in modelos]


class ConversaRepositorySql:
    def __init__(self, sessoes: async_sessionmaker[AsyncSession]) -> None:
        self._sessoes = sessoes

    async def obter_aberta(self, lead_id: UUID) -> Conversa | None:
        async with self._sessoes() as sessao:
            modelo = await sessao.scalar(
                select(ConversaModel)
                .where(
                    ConversaModel.lead_id == lead_id,
                    ConversaModel.status == StatusConversa.ABERTA.value,
                )
                .order_by(ConversaModel.atualizada_em.desc())
                .limit(1)
            )
        return _conversa(modelo) if modelo else None

    async def salvar(self, conversa: Conversa) -> None:
        stmt = insert(ConversaModel).values(
            id=conversa.id,
            lead_id=conversa.lead_id,
            canal=conversa.canal.value,
            status=conversa.status.value,
            iniciada_em=conversa.iniciada_em,
            atualizada_em=conversa.atualizada_em,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[ConversaModel.id],
            set_={"status": stmt.excluded.status, "atualizada_em": stmt.excluded.atualizada_em},
        )
        async with self._sessoes.begin() as sessao:
            await sessao.execute(stmt)

    async def adicionar_mensagem(self, mensagem: Mensagem) -> None:
        async with self._sessoes.begin() as sessao:
            sessao.add(
                MensagemModel(
                    id=mensagem.id,
                    conversa_id=mensagem.conversa_id,
                    papel=mensagem.papel.value,
                    texto=mensagem.texto,
                    metadados=dict(mensagem.metadados),
                    criada_em=mensagem.criada_em,
                    status=mensagem.status.value,
                )
            )

    async def ultimas_mensagens(
        self, conversa_id: UUID, limite: int, *, incluir_pendentes: bool = False
    ) -> list[Mensagem]:
        stmt = select(MensagemModel).where(MensagemModel.conversa_id == conversa_id)
        if not incluir_pendentes:
            stmt = stmt.where(MensagemModel.status != StatusMensagem.PENDENTE.value)
        stmt = stmt.order_by(MensagemModel.criada_em.desc(), MensagemModel.id.desc()).limit(limite)
        async with self._sessoes() as sessao:
            modelos = (await sessao.scalars(stmt)).all()
        return [_mensagem(m) for m in reversed(modelos)]

    async def pendentes(self, conversa_id: UUID) -> list[Mensagem]:
        async with self._sessoes() as sessao:
            modelos = (
                await sessao.scalars(
                    select(MensagemModel)
                    .where(
                        MensagemModel.conversa_id == conversa_id,
                        MensagemModel.status == StatusMensagem.PENDENTE.value,
                    )
                    .order_by(MensagemModel.criada_em, MensagemModel.id)
                )
            ).all()
        return [_mensagem(m) for m in modelos]

    async def marcar_status(self, ids: Sequence[UUID], status: StatusMensagem) -> None:
        if not ids:
            return
        async with self._sessoes.begin() as sessao:
            await sessao.execute(
                update(MensagemModel)
                .where(MensagemModel.id.in_(list(ids)))
                .values(status=status.value)
            )

    async def leads_com_pendentes(self) -> list[UUID]:
        async with self._sessoes() as sessao:
            ids = (
                await sessao.scalars(
                    select(ConversaModel.lead_id)
                    .join(MensagemModel, MensagemModel.conversa_id == ConversaModel.id)
                    .where(MensagemModel.status == StatusMensagem.PENDENTE.value)
                    .distinct()
                )
            ).all()
        return list(ids)


class LeadEventoRepositorySql:
    def __init__(self, sessoes: async_sessionmaker[AsyncSession]) -> None:
        self._sessoes = sessoes

    async def registrar(self, eventos: Sequence[EventoLead]) -> None:
        if not eventos:
            return
        async with self._sessoes.begin() as sessao:
            sessao.add_all(
                LeadEventoModel(
                    id=e.id,
                    lead_id=e.lead_id,
                    tipo=e.tipo.value,
                    payload=dict(e.payload),
                    ocorrido_em=e.ocorrido_em,
                )
                for e in eventos
            )

    async def listar(self, lead_id: UUID, limite: int = 200) -> list[EventoLead]:
        async with self._sessoes() as sessao:
            modelos = (
                await sessao.scalars(
                    select(LeadEventoModel)
                    .where(LeadEventoModel.lead_id == lead_id)
                    .order_by(LeadEventoModel.ocorrido_em, LeadEventoModel.seq)
                    .limit(limite)
                )
            ).all()
        return [
            EventoLead(m.lead_id, TipoEvento(m.tipo), m.ocorrido_em, dict(m.payload), m.id)
            for m in modelos
        ]
