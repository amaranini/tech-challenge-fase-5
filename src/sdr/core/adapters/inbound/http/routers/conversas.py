"""Canal WEB, assíncrono: o POST só registra a mensagem (202) e o turno é processado
depois do debounce; o front acompanha por polling em GET /conversas/{lead_id}/mensagens.

O `lead_id` público é o identificador do remetente no canal web; leads de outros canais
aparecem como "<canal>:<remetente>" (ex.: "whatsapp:+5511..."), só para leitura aqui — o
WhatsApp entra pelo webhook (routers/whatsapp_twilio.py), no mesmo ReceberMensagem.

Segurança (ADR 015): rotas públicas (o chat do site), com rate limit por IP e por lead.
Histórico de lead de outro canal só com credencial de admin; sem ela, a resposta não traz
os metadados internos (rastro, envio, status de entrega).
"""

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from pydantic import BaseModel, Field

from sdr.core.adapters.inbound.http.dependencias import (
    obter_obter_historico,
    obter_receber_mensagem,
)
from sdr.core.adapters.inbound.http.lead_publico import identificar
from sdr.core.adapters.inbound.http.protecao import (
    eh_admin,
    ip_cliente,
    limitar,
    obter_protecao,
)
from sdr.core.application.dto.mensagem_recebida import MensagemRecebida
from sdr.core.application.use_cases.consultar_conversas import ObterHistorico
from sdr.core.application.use_cases.receber_mensagem import (
    MensagemInvalidaError,
    ReceberMensagem,
)
from sdr.core.domain.conversa import Canal, Mensagem, Papel, StatusEntrega, StatusMensagem

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
    envio: dict[str, Any] | None = Field(
        default=None,
        description="Saída: modo (texto | template | bloqueado), template e variáveis",
    )
    entrega: StatusEntrega | None = Field(
        default=None, description="Status informado pelo provedor (enviada/entregue/lida/falhou)"
    )
    anexos: list[dict[str, Any]] = Field(default_factory=list, description="Do lead")
    trace_id: str | None = Field(default=None, description="Rastro no Langfuse (observabilidade)")
    trace_url: str | None = None

    def publica(self) -> "MensagemResposta":
        """Sem o que é interno da operação (o lead não precisa ver)."""
        return self.model_copy(
            update={"trace_id": None, "trace_url": None, "envio": None, "entrega": None}
        )

    @classmethod
    def de_dominio(cls, m: Mensagem) -> "MensagemResposta":
        citados = m.metadados.get("itens_citados")
        envio = m.metadados.get("envio")
        midias = m.metadados.get("midias")
        trace = m.metadados.get("trace")
        trace = trace if isinstance(trace, dict) else {}
        return cls(
            trace_id=trace.get("id"),
            trace_url=trace.get("url"),
            envio=dict(envio) if isinstance(envio, dict) else None,
            entrega=m.entrega,
            anexos=[{"tipo": a.get("tipo")} for a in midias] if isinstance(midias, list) else [],
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
    request: Request,
    envio: EnvioMensagem,
    receber: Annotated[ReceberMensagem, Depends(obter_receber_mensagem)],
) -> RecebimentoResposta:
    protecao = obter_protecao(request)
    await limitar(request, f"ip:{ip_cliente(request)}", protecao.limite_ip_por_minuto)
    await limitar(request, f"remetente:web:{envio.lead_id}", protecao.limite_remetente_por_minuto)
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
    request: Request,
    lead_id: Annotated[str, Path(min_length=1, max_length=100)],
    obter: Annotated[ObterHistorico, Depends(obter_obter_historico)],
) -> HistoricoResposta:
    canal, remetente = identificar(lead_id)
    admin = eh_admin(request)
    if not admin:
        if canal is not Canal.WEB:  # ex.: conversa de WhatsApp só para a equipe
            raise HTTPException(401, "credencial ausente ou inválida")
        protecao = obter_protecao(request)
        await limitar(
            request, f"ip-leitura:{ip_cliente(request)}", 4 * protecao.limite_ip_por_minuto
        )
    resultado = await obter.executar(canal, remetente)
    mensagens = [MensagemResposta.de_dominio(m) for m in resultado.mensagens]
    return HistoricoResposta(
        lead_id=lead_id,
        conversa_id=resultado.conversa.id if resultado.conversa else None,
        processando=resultado.processando,
        mensagens=mensagens if admin else [m.publica() for m in mensagens],
    )
