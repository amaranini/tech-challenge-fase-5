"""Canal WEB: o `lead_id` público é o identificador do remetente no canal web.

O WhatsApp (Dia 4) terá seu próprio router de webhook, convertendo para o mesmo
MensagemRecebida e chamando o mesmo ProcessarMensagemRecebida.
"""

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from sdr.core.adapters.inbound.http.dependencias import (
    obter_listar_leads,
    obter_obter_historico,
    obter_processar_mensagem,
)
from sdr.core.application.dto.mensagem_recebida import MensagemRecebida
from sdr.core.application.ports.llm import LLMIndisponivelError
from sdr.core.application.use_cases.consultar_conversas import ListarLeads, ObterHistorico
from sdr.core.application.use_cases.processar_mensagem_recebida import (
    MensagemInvalidaError,
    ProcessarMensagemRecebida,
)
from sdr.core.domain.catalogo import ItemCatalogo
from sdr.core.domain.conversa import Canal, Mensagem, Papel

router = APIRouter(tags=["conversas"])

LeadId = Annotated[str, Field(min_length=1, max_length=100, pattern=r"^[\w.@+-]+$")]


class EnvioMensagem(BaseModel):
    lead_id: LeadId = Field(examples=["lead-ana"])
    texto: str = Field(min_length=1, max_length=4000, examples=["Oi! Procuro um apê na zona sul"])


class ItemSugerido(BaseModel):
    id: str
    titulo: str
    resumo: str
    atributos: dict[str, Any]

    @classmethod
    def de_dominio(cls, item: ItemCatalogo) -> "ItemSugerido":
        return cls(
            id=item.id, titulo=item.titulo, resumo=item.resumo, atributos=dict(item.atributos)
        )


class MensagemResposta(BaseModel):
    id: UUID
    papel: Papel
    texto: str
    criada_em: datetime
    itens_citados: list[dict[str, Any]] = Field(default_factory=list)

    @classmethod
    def de_dominio(cls, m: Mensagem) -> "MensagemResposta":
        citados = m.metadados.get("itens_citados")
        return cls(
            id=m.id,
            papel=m.papel,
            texto=m.texto,
            criada_em=m.criada_em,
            itens_citados=list(citados) if isinstance(citados, list) else [],
        )


class EnvioResposta(BaseModel):
    lead_id: str
    conversa_id: UUID
    resposta: MensagemResposta
    itens_sugeridos: list[ItemSugerido]


class HistoricoResposta(BaseModel):
    lead_id: str
    conversa_id: UUID | None
    mensagens: list[MensagemResposta]


class LeadResumo(BaseModel):
    lead_id: str
    criado_em: datetime
    total_mensagens: int
    ultima_interacao_em: datetime | None


@router.post("/conversas/mensagens", response_model=EnvioResposta)
async def enviar_mensagem(
    envio: EnvioMensagem,
    processar: Annotated[ProcessarMensagemRecebida, Depends(obter_processar_mensagem)],
) -> EnvioResposta:
    try:
        resultado = await processar.executar(
            MensagemRecebida(canal=Canal.WEB, remetente_id=envio.lead_id, texto=envio.texto)
        )
    except MensagemInvalidaError as erro:
        raise HTTPException(422, str(erro)) from erro
    except LLMIndisponivelError as erro:
        raise HTTPException(503, f"Agente indisponível: {erro}") from erro

    return EnvioResposta(
        lead_id=resultado.lead.remetente_id,
        conversa_id=resultado.conversa.id,
        resposta=MensagemResposta.de_dominio(resultado.resposta),
        itens_sugeridos=[ItemSugerido.de_dominio(i) for i in resultado.itens_sugeridos],
    )


@router.get("/conversas/mensagens", response_model=HistoricoResposta)
async def historico(
    lead_id: Annotated[str, Query(min_length=1, max_length=100)],
    obter: Annotated[ObterHistorico, Depends(obter_obter_historico)],
) -> HistoricoResposta:
    resultado = await obter.executar(Canal.WEB, lead_id)
    return HistoricoResposta(
        lead_id=lead_id,
        conversa_id=resultado.conversa.id if resultado.conversa else None,
        mensagens=[MensagemResposta.de_dominio(m) for m in resultado.mensagens],
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
