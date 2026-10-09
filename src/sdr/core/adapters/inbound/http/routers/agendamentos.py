"""Agendamentos com responsáveis (agenda do time comercial; dashboard no Dia 4)."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from sdr.core.adapters.inbound.http.dependencias import obter_listar_agendamentos
from sdr.core.adapters.inbound.http.lead_publico import id_publico
from sdr.core.application.use_cases.consultar_agenda_e_resumo import ListarAgendamentos
from sdr.core.domain.agenda import Agendamento, StatusAgendamento

router = APIRouter(tags=["agendamentos"])


class ResponsavelResposta(BaseModel):
    id: UUID
    nome: str
    titulo: str


class AgendamentoResposta(BaseModel):
    id: UUID
    lead_id: str | None  # lead_id público (remetente no canal)
    tipo: str
    modalidade: str
    status: StatusAgendamento
    inicio: datetime
    fim: datetime
    responsavel: ResponsavelResposta
    itens: list[str]
    criado_em: datetime

    @classmethod
    def de_dominio(cls, ag: Agendamento, lead_id: str | None) -> "AgendamentoResposta":
        r = ag.responsavel
        return cls(
            id=ag.id,
            lead_id=lead_id,
            tipo=ag.tipo,
            modalidade=ag.modalidade,
            status=ag.status,
            inicio=ag.inicio,
            fim=ag.fim,
            responsavel=ResponsavelResposta(id=r.id, nome=r.nome, titulo=r.titulo),
            itens=list(ag.itens),
            criado_em=ag.criado_em,
        )


@router.get("/agendamentos", response_model=list[AgendamentoResposta])
async def listar_agendamentos(
    listar: Annotated[ListarAgendamentos, Depends(obter_listar_agendamentos)],
    a_partir_de: Annotated[
        datetime | None, Query(description="Só os que terminam depois deste instante")
    ] = None,
    status: StatusAgendamento | None = None,
    limite: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[AgendamentoResposta]:
    """Em ordem de início."""
    encontrados = await listar.executar(a_partir_de=a_partir_de, status=status, limite=limite)
    return [
        AgendamentoResposta.de_dominio(e.agendamento, id_publico(e.lead) if e.lead else None)
        for e in encontrados
    ]
