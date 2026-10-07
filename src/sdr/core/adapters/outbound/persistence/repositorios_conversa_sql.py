from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sdr.core.adapters.outbound.persistence.modelos import ConversaModel, LeadModel, MensagemModel
from sdr.core.application.ports.repositorios import ResumoLead
from sdr.core.domain.conversa import (
    Canal,
    Conversa,
    Lead,
    Mensagem,
    Papel,
    StatusConversa,
)


def _lead(m: LeadModel) -> Lead:
    return Lead(
        id=m.id,
        canal=Canal(m.canal),
        remetente_id=m.remetente_id,
        criado_em=m.criado_em,
        nome=m.nome,
        ficha_qualificacao=dict(m.ficha_qualificacao or {}),
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
    )


class LeadRepositorySql:
    def __init__(self, sessoes: async_sessionmaker[AsyncSession]) -> None:
        self._sessoes = sessoes

    async def obter_por_remetente(self, canal: Canal, remetente_id: str) -> Lead | None:
        async with self._sessoes() as sessao:
            modelo = await sessao.scalar(
                select(LeadModel).where(
                    LeadModel.canal == canal.value, LeadModel.remetente_id == remetente_id
                )
            )
        return _lead(modelo) if modelo else None

    async def salvar(self, lead: Lead) -> None:
        valores = {
            "id": lead.id,
            "canal": lead.canal.value,
            "remetente_id": lead.remetente_id,
            "nome": lead.nome,
            "ficha_qualificacao": dict(lead.ficha_qualificacao),
            "criado_em": lead.criado_em,
        }
        stmt = insert(LeadModel).values(valores)
        stmt = stmt.on_conflict_do_update(
            index_elements=[LeadModel.id],
            set_={
                "nome": stmt.excluded.nome,
                "ficha_qualificacao": stmt.excluded.ficha_qualificacao,
                "atualizado_em": func.now(),
            },
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
                )
            )

    async def ultimas_mensagens(self, conversa_id: UUID, limite: int) -> list[Mensagem]:
        async with self._sessoes() as sessao:
            modelos = (
                await sessao.scalars(
                    select(MensagemModel)
                    .where(MensagemModel.conversa_id == conversa_id)
                    .order_by(MensagemModel.criada_em.desc(), MensagemModel.id.desc())
                    .limit(limite)
                )
            ).all()
        return [_mensagem(m) for m in reversed(modelos)]
