"""FollowUpRepository em Postgres: fila `followups_agendados` consumida com SKIP LOCKED.

`reservar_vencidos` pega os vencidos numa transação curta (`FOR UPDATE SKIP LOCKED`) e os
marca PROCESSANDO: vários workers nunca pegam o mesmo item, e a geração da mensagem (LLM)
não segura lock. Item PROCESSANDO há muito tempo (worker caiu) volta a ser elegível.
"""

from collections.abc import Sequence
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sdr.core.adapters.outbound.persistence.modelos import FollowUpModel
from sdr.core.domain.followup import FollowUp, SituacaoLead, StatusFollowUp, TipoFollowUp


def _followup(m: FollowUpModel) -> FollowUp:
    return FollowUp(
        id=m.id,
        lead_id=m.lead_id,
        tipo=TipoFollowUp(m.tipo),
        executar_em=m.executar_em,
        criado_em=m.criado_em,
        etapa=m.etapa,
        situacao=SituacaoLead(m.situacao) if m.situacao else None,
        status=StatusFollowUp(m.status),
        motivo=m.motivo,
        referencia=m.referencia,
        ignorar_horario=m.ignorar_horario,
    )


class FollowUpRepositorySql:
    def __init__(self, sessoes: async_sessionmaker[AsyncSession]) -> None:
        self._sessoes = sessoes

    async def agendar(self, followup: FollowUp) -> None:
        async with self._sessoes.begin() as sessao:
            sessao.add(
                FollowUpModel(
                    id=followup.id,
                    lead_id=followup.lead_id,
                    tipo=followup.tipo.value,
                    etapa=followup.etapa,
                    situacao=followup.situacao.value if followup.situacao else None,
                    executar_em=followup.executar_em,
                    status=followup.status.value,
                    motivo=followup.motivo,
                    referencia=followup.referencia,
                    ignorar_horario=followup.ignorar_horario,
                    criado_em=followup.criado_em,
                )
            )

    async def cancelar_pendentes(
        self, lead_id: UUID, tipos: Sequence[TipoFollowUp], motivo: str
    ) -> int:
        async with self._sessoes.begin() as sessao:
            resultado = await sessao.execute(
                update(FollowUpModel)
                .where(
                    FollowUpModel.lead_id == lead_id,
                    FollowUpModel.status == StatusFollowUp.PENDENTE.value,
                    FollowUpModel.tipo.in_([t.value for t in tipos]),
                )
                .values(status=StatusFollowUp.CANCELADO.value, motivo=motivo)
            )
        return int(resultado.rowcount)  # type: ignore[attr-defined]

    async def reservar_vencidos(
        self, agora: datetime, limite: int, *, travado_ha: float = 600
    ) -> list[FollowUp]:
        travado = agora - timedelta(seconds=travado_ha)
        async with self._sessoes.begin() as sessao:
            modelos = list(
                await sessao.scalars(
                    select(FollowUpModel)
                    .where(
                        FollowUpModel.executar_em <= agora,
                        or_(
                            FollowUpModel.status == StatusFollowUp.PENDENTE.value,
                            (FollowUpModel.status == StatusFollowUp.PROCESSANDO.value)
                            & (FollowUpModel.reservado_em < travado),
                        ),
                    )
                    .order_by(FollowUpModel.executar_em)
                    .limit(limite)
                    .with_for_update(skip_locked=True)
                )
            )
            for m in modelos:
                m.status = StatusFollowUp.PROCESSANDO.value
                m.reservado_em = agora
            return [_followup(m) for m in modelos]

    async def concluir(self, followup_id: UUID, status: StatusFollowUp, motivo: str | None) -> None:
        async with self._sessoes.begin() as sessao:
            await sessao.execute(
                update(FollowUpModel)
                .where(FollowUpModel.id == followup_id)
                .values(status=status.value, motivo=motivo)
            )

    async def reagendar(self, followup: FollowUp) -> None:
        async with self._sessoes.begin() as sessao:
            await sessao.execute(
                update(FollowUpModel)
                .where(FollowUpModel.id == followup.id)
                .values(
                    status=StatusFollowUp.PENDENTE.value,
                    executar_em=followup.executar_em,
                    motivo=followup.motivo,
                    ignorar_horario=followup.ignorar_horario,
                    reservado_em=None,
                )
            )

    async def pendentes(self, lead_id: UUID) -> list[FollowUp]:
        async with self._sessoes() as sessao:
            modelos = await sessao.scalars(
                select(FollowUpModel)
                .where(
                    FollowUpModel.lead_id == lead_id,
                    FollowUpModel.status == StatusFollowUp.PENDENTE.value,
                )
                .order_by(FollowUpModel.executar_em)
            )
            return [_followup(m) for m in modelos]
