"""Leads do canal web: lista (seletor do chat) e estado de qualificação (painel).

O `lead_id` público é o identificador do remetente no canal web, como em /conversas.
A ficha é opaca para o core: os campos e valores vêm da vertical ativa.
"""

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, Field

from sdr.core.adapters.inbound.http.dependencias import (
    obter_listar_leads,
    obter_obter_lead,
    obter_obter_resumo,
)
from sdr.core.adapters.inbound.http.routers.agendamentos import AgendamentoResposta
from sdr.core.adapters.inbound.http.routers.atendimentos import AtendimentoResposta
from sdr.core.application.use_cases.consultar_agenda_e_resumo import ObterResumo
from sdr.core.application.use_cases.consultar_conversas import ListarLeads
from sdr.core.application.use_cases.obter_lead import EstadoLead, ObterLead
from sdr.core.domain.conversa import Canal
from sdr.core.domain.eventos import EventoLead
from sdr.core.domain.resumo import Resumo

router = APIRouter(tags=["leads"])


class LeadResumo(BaseModel):
    lead_id: str
    criado_em: datetime
    total_mensagens: int
    ultima_interacao_em: datetime | None


class EventoResposta(BaseModel):
    id: UUID
    tipo: str
    ocorrido_em: datetime
    payload: dict[str, Any]

    @classmethod
    def de_dominio(cls, evento: EventoLead) -> "EventoResposta":
        return cls(
            id=evento.id,
            tipo=evento.tipo.value,
            ocorrido_em=evento.ocorrido_em,
            payload=dict(evento.payload),
        )


class LeadDetalhe(BaseModel):
    lead_id: str
    canal: Canal
    nome: str | None
    criado_em: datetime
    intencao: str | None = Field(description="Intenção atual (null enquanto indefinida)")
    ficha: dict[str, Any] = Field(description="Ficha da intenção atual")
    fichas: dict[str, dict[str, Any]] = Field(
        description="Todas as fichas por intenção (trocar de intenção não apaga nenhuma)"
    )
    campos_faltantes: list[str] = Field(
        description="Campos da intenção atual ainda vazios, em ordem de prioridade"
    )
    score: int | None
    classificacao: str | None
    score_motivos: list[str]
    proxima_acao: str | None
    qualificado_em: datetime | None
    eventos: list[EventoResposta] = Field(description="Trilha de lead_eventos, em ordem")
    agendamento: AgendamentoResposta | None = Field(
        default=None, description="Agendamento ativo e ainda por acontecer"
    )
    atendimento: AtendimentoResposta = Field(description="IA × humano (fila, quem atende)")

    @classmethod
    def de_estado(cls, estado: EstadoLead) -> "LeadDetalhe":
        lead, q = estado.lead, estado.lead.qualificacao
        return cls(
            lead_id=lead.remetente_id,
            canal=lead.canal,
            nome=lead.nome,
            criado_em=lead.criado_em,
            intencao=q.intencao_atual,
            ficha=dict(q.ficha),
            fichas={intencao: dict(ficha) for intencao, ficha in q.fichas.items()},
            campos_faltantes=estado.campos_faltantes,
            score=q.score.pontos if q.score else None,
            classificacao=q.score.classificacao.value if q.score else None,
            score_motivos=list(q.score.motivos) if q.score else [],
            proxima_acao=q.proxima_acao,
            qualificado_em=q.qualificado_em,
            eventos=[EventoResposta.de_dominio(e) for e in estado.eventos],
            agendamento=(
                AgendamentoResposta.de_dominio(estado.agendamento, lead.remetente_id)
                if estado.agendamento
                else None
            ),
            atendimento=AtendimentoResposta.de_dominio(lead.atendimento_atual),
        )


@router.get("/leads", response_model=list[LeadResumo])
async def listar_leads(
    listar: Annotated[ListarLeads, Depends(obter_listar_leads)],
    limite: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[LeadResumo]:
    resumos = await listar.executar(Canal.WEB, limite)
    return [
        LeadResumo(
            lead_id=r.lead.remetente_id,
            criado_em=r.lead.criado_em,
            total_mensagens=r.total_mensagens,
            ultima_interacao_em=r.ultima_interacao_em,
        )
        for r in resumos
    ]


@router.get(
    "/leads/{lead_id}",
    response_model=LeadDetalhe,
    responses={404: {"description": "Lead não encontrado"}},
)
async def obter_lead(
    lead_id: Annotated[str, Path(min_length=1, max_length=100)],
    obter: Annotated[ObterLead, Depends(obter_obter_lead)],
) -> LeadDetalhe:
    estado = await obter.executar(Canal.WEB, lead_id)
    if estado is None:
        raise HTTPException(404, f"lead {lead_id!r} não encontrado")
    return LeadDetalhe.de_estado(estado)


class SecaoResposta(BaseModel):
    chave: str
    titulo: str
    tipo: str
    conteudo: Any = Field(description='Texto, lista ou objeto; "não informado" quando vazio')


class ResumoResposta(BaseModel):
    lead_id: str
    versao: int
    versoes: list[int]
    gerado_em: datetime
    gatilho: str
    titulo: str
    template_versao: str
    secoes: list[SecaoResposta]
    descartados: list[str] = Field(
        description="O que a ancoragem removeu por falta de lastro na conversa (auditoria)"
    )
    modelo: str | None

    @classmethod
    def de_dominio(cls, lead_id: str, resumo: Resumo, versoes: list[int]) -> "ResumoResposta":
        return cls(
            lead_id=lead_id,
            versao=resumo.versao,
            versoes=versoes,
            gerado_em=resumo.gerado_em,
            gatilho=resumo.gatilho,
            titulo=resumo.titulo,
            template_versao=resumo.template_versao,
            secoes=[
                SecaoResposta(
                    chave=s.chave, titulo=s.titulo, tipo=s.tipo.value, conteudo=s.conteudo
                )
                for s in resumo.secoes
            ],
            descartados=list(resumo.descartados),
            modelo=resumo.modelo,
        )


@router.get(
    "/leads/{lead_id}/resumo",
    response_model=ResumoResposta,
    responses={404: {"description": "Lead não encontrado ou ainda sem resumo"}},
)
async def obter_resumo(
    lead_id: Annotated[str, Path(min_length=1, max_length=100)],
    obter: Annotated[ObterResumo, Depends(obter_obter_resumo)],
    versao: Annotated[int | None, Query(ge=1)] = None,
) -> ResumoResposta:
    """Resumo para o responsável (última versão, ou `?versao=N`)."""
    encontrado = await obter.executar(Canal.WEB, lead_id, versao)
    if encontrado is None:
        raise HTTPException(404, f"lead {lead_id!r} não encontrado")
    if encontrado.resumo is None:
        raise HTTPException(404, f"lead {lead_id!r} ainda não tem resumo")
    return ResumoResposta.de_dominio(lead_id, encontrado.resumo, encontrado.versoes)
