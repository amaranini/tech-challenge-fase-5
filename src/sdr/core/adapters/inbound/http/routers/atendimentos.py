"""Atendimento humano: fila, assumir, devolver e responder ao lead (tela "Fila").

O `lead_id` é o público do canal web (como nas demais rotas). Transição fora da máquina de
estados (ex.: devolver quem não está em atendimento humano) responde 409.
"""

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, Field

from sdr.core.adapters.inbound.http.dependencias import (
    obter_assumir_atendimento,
    obter_devolver_atendimento,
    obter_enviar_mensagem_responsavel,
    obter_listar_atendimentos,
)
from sdr.core.adapters.inbound.http.lead_publico import id_publico, identificar
from sdr.core.application.use_cases.atendimento import (
    AssumirAtendimento,
    AtendimentoAlteradoError,
    AtendimentoNaoAssumidoError,
    DevolverAtendimento,
    EnviarMensagemResponsavel,
    ItemFila,
    LeadNaoEncontradoError,
    ListarAtendimentos,
    ResultadoAtendimento,
)
from sdr.core.domain.atendimento import Atendimento, EstadoAtendimento, TransicaoInvalidaError
from sdr.core.domain.conversa import Canal

router = APIRouter(prefix="/atendimentos", tags=["atendimentos"])
LeadId = Annotated[str, Path(min_length=1, max_length=100)]


class AtendimentoResposta(BaseModel):
    estado: EstadoAtendimento
    desde: datetime | None
    na_fila_desde: datetime | None
    motivo: str | None
    responsavel: str | None

    @classmethod
    def de_dominio(cls, a: Atendimento) -> "AtendimentoResposta":
        return cls(
            estado=a.estado,
            desde=a.desde,
            na_fila_desde=a.na_fila_desde,
            motivo=a.motivo.value if a.motivo else None,
            responsavel=a.responsavel,
        )


class ItemFilaResposta(BaseModel):
    lead_id: str
    canal: Canal
    nome: str | None
    espera_segundos: int
    atendimento: AtendimentoResposta
    intencao: str | None
    score: int | None

    @classmethod
    def de_dominio(cls, item: ItemFila) -> "ItemFilaResposta":
        lead = item.lead
        q = lead.qualificacao
        return cls(
            lead_id=id_publico(lead),
            canal=lead.canal,
            nome=lead.nome,
            espera_segundos=item.espera_segundos,
            atendimento=AtendimentoResposta.de_dominio(lead.atendimento_atual),
            intencao=q.intencao_atual,
            score=q.score.pontos if q.score else None,
        )


class Assumir(BaseModel):
    responsavel: str = Field(min_length=1, max_length=100, examples=["Rafael Souza"])


class MensagemDoResponsavel(BaseModel):
    texto: str = Field(min_length=1, max_length=4000)
    responsavel: str | None = Field(default=None, max_length=100)


class MensagemEnviada(BaseModel):
    id: UUID
    papel: str
    texto: str
    criada_em: datetime
    envio: str = Field(description="texto | template (fora da janela) | bloqueado")
    entrega: str | None = None


def _resposta(resultado: ResultadoAtendimento) -> AtendimentoResposta:
    return AtendimentoResposta.de_dominio(resultado.atendimento)


ERROS: dict[int | str, dict[str, Any]] = {
    404: {"description": "Lead não encontrado"},
    409: {"description": "Transição inválida"},
}


@router.get("/fila", response_model=list[ItemFilaResposta])
async def fila(
    listar: Annotated[ListarAtendimentos, Depends(obter_listar_atendimentos)],
) -> list[ItemFilaResposta]:
    """Leads aguardando uma pessoa da equipe, do que espera há mais tempo ao mais recente."""
    return [ItemFilaResposta.de_dominio(i) for i in await listar.executar()]


@router.get("", response_model=list[ItemFilaResposta])
async def por_estado(
    listar: Annotated[ListarAtendimentos, Depends(obter_listar_atendimentos)],
    estado: Annotated[list[EstadoAtendimento], Query()] = [  # noqa: B006
        EstadoAtendimento.ATENDIMENTO_HUMANO
    ],
) -> list[ItemFilaResposta]:
    """Leads por estado de atendimento (padrão: em atendimento humano)."""
    return [ItemFilaResposta.de_dominio(i) for i in await listar.executar(tuple(estado))]


@router.post("/{lead_id}/assumir", response_model=AtendimentoResposta, responses=ERROS)
async def assumir(
    lead_id: LeadId,
    corpo: Assumir,
    caso_de_uso: Annotated[AssumirAtendimento, Depends(obter_assumir_atendimento)],
) -> AtendimentoResposta:
    try:
        return _resposta(await caso_de_uso.executar(*identificar(lead_id), corpo.responsavel))
    except LeadNaoEncontradoError:
        raise HTTPException(404, f"lead {lead_id!r} não encontrado") from None
    except (TransicaoInvalidaError, AtendimentoAlteradoError) as erro:
        raise HTTPException(409, str(erro)) from None


@router.post("/{lead_id}/devolver", response_model=AtendimentoResposta, responses=ERROS)
async def devolver(
    lead_id: LeadId,
    caso_de_uso: Annotated[DevolverAtendimento, Depends(obter_devolver_atendimento)],
) -> AtendimentoResposta:
    """Devolve a conversa para a IA (que retoma sabendo o que a equipe disse)."""
    try:
        return _resposta(await caso_de_uso.executar(*identificar(lead_id)))
    except LeadNaoEncontradoError:
        raise HTTPException(404, f"lead {lead_id!r} não encontrado") from None
    except (TransicaoInvalidaError, AtendimentoAlteradoError) as erro:
        raise HTTPException(409, str(erro)) from None


@router.post(
    "/{lead_id}/mensagens", status_code=201, response_model=MensagemEnviada, responses=ERROS
)
async def responder(
    lead_id: LeadId,
    corpo: MensagemDoResponsavel,
    caso_de_uso: Annotated[EnviarMensagemResponsavel, Depends(obter_enviar_mensagem_responsavel)],
) -> MensagemEnviada:
    """O responsável escreve ao lead (sai pelo CanalMensagemPort do canal do lead)."""
    try:
        m = await caso_de_uso.executar(*identificar(lead_id), corpo.texto, corpo.responsavel)
    except LeadNaoEncontradoError:
        raise HTTPException(404, f"lead {lead_id!r} não encontrado") from None
    except AtendimentoNaoAssumidoError as erro:
        raise HTTPException(409, str(erro)) from None
    envio = m.metadados.get("envio")
    return MensagemEnviada(
        id=m.id,
        papel=m.papel.value,
        texto=m.texto,
        criada_em=m.criada_em,
        envio=str(envio.get("modo")) if isinstance(envio, dict) else "texto",
        entrega=m.entrega.value if m.entrega else None,
    )
