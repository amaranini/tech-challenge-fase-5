"""Canal WEB, assíncrono: o POST só registra a mensagem (202) e o turno é processado
depois do debounce; o front acompanha por polling em GET /conversas/{lead_id}/mensagens.

O `lead_id` público é o identificador do remetente no canal web. O WhatsApp (Dia 4) terá
seu router de webhook chamando o mesmo ReceberMensagem.
"""

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel, Field

from sdr.core.adapters.inbound.http.dependencias import (
    obter_obter_historico,
    obter_receber_mensagem,
)
from sdr.core.application.dto.mensagem_recebida import MensagemRecebida
from sdr.core.application.use_cases.consultar_conversas import ObterHistorico
from sdr.core.application.use_cases.receber_mensagem import (
    MensagemInvalidaError,
    ReceberMensagem,
)
from sdr.core.domain.conversa import Canal, Mensagem, Papel, StatusMensagem

router = APIRouter(tags=["conversas"])

LeadId = Annotated[str, Field(min_length=1, max_length=100, pattern=r"^[\w.@+-]+$")]


class EnvioMensagem(BaseModel):
    lead_id: LeadId = Field(examples=["lead-ana"])
    texto: str = Field(min_length=1, max_length=4000, examples=["Oi! Procuro um apê na zona sul"])


class MensagemResposta(BaseModel):
    id: UUID
    papel: Papel
    texto: str
    criada_em: datetime
    status: StatusMensagem
    itens_citados: list[dict[str, Any]] = Field(default_factory=list)
    responsavel: str | None = Field(
        default=None, description="Quem da equipe escreveu (papel = responsavel)"
    )

    @classmethod
    def de_dominio(cls, m: Mensagem) -> "MensagemResposta":
        citados = m.metadados.get("itens_citados")
        return cls(
            id=m.id,
            papel=m.papel,
            texto=m.texto,
            criada_em=m.criada_em,
            status=m.status,
            itens_citados=list(citados) if isinstance(citados, list) else [],
            responsavel=(
                str(m.metadados.get("responsavel")) if m.metadados.get("responsavel") else None
            ),
        )


class RecebimentoResposta(BaseModel):
    lead_id: str
    conversa_id: UUID
    mensagem_id: UUID
    status: StatusMensagem


class HistoricoResposta(BaseModel):
    lead_id: str
    conversa_id: UUID | None
    processando: bool = Field(description="Há turno agendado/em andamento (Lia digitando)")
    mensagens: list[MensagemResposta]


@router.post("/conversas/mensagens", response_model=RecebimentoResposta, status_code=202)
async def receber_mensagem(
    envio: EnvioMensagem,
    receber: Annotated[ReceberMensagem, Depends(obter_receber_mensagem)],
) -> RecebimentoResposta:
    try:
        resultado = await receber.executar(
            MensagemRecebida(canal=Canal.WEB, remetente_id=envio.lead_id, texto=envio.texto)
        )
    except MensagemInvalidaError as erro:
        raise HTTPException(422, str(erro)) from erro
    return RecebimentoResposta(
        lead_id=resultado.lead.remetente_id,
        conversa_id=resultado.conversa.id,
        mensagem_id=resultado.mensagem.id,
        status=resultado.mensagem.status,
    )


@router.get("/conversas/{lead_id}/mensagens", response_model=HistoricoResposta)
async def historico(
    lead_id: Annotated[str, Path(min_length=1, max_length=100)],
    obter: Annotated[ObterHistorico, Depends(obter_obter_historico)],
) -> HistoricoResposta:
    resultado = await obter.executar(Canal.WEB, lead_id)
    return HistoricoResposta(
        lead_id=lead_id,
        conversa_id=resultado.conversa.id if resultado.conversa else None,
        processando=resultado.processando,
        mensagens=[MensagemResposta.de_dominio(m) for m in resultado.mensagens],
    )
