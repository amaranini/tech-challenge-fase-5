"""DadosTitularPort em Postgres: exporta e anonimiza tudo o que é do lead (ADR 015)."""

from collections.abc import Mapping
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sdr.core.adapters.outbound.persistence.modelos import (
    AgendamentoModel,
    ConversaModel,
    CrmRegistroModel,
    FollowUpModel,
    LeadEventoModel,
    LeadModel,
    MensagemModel,
    ResumoHandoffModel,
)

TEXTO_ANONIMIZADO = "[anonimizado]"
# Metadados de mensagem que podem trazer dado pessoal (anexos, texto original, variáveis
# de template com o nome do lead); o resto (modelo, tokens, roteamento, trace) fica.
METADADOS_PESSOAIS = ("midias", "texto_lead", "envio")
# Valores nos payloads de evento (campos da ficha, fala do opt-out...): o TIPO fica.
PAYLOAD_PESSOAL = ("valor", "de", "para", "fala", "nome", "texto")


def _json(valor: object) -> object:
    if isinstance(valor, datetime):
        return valor.isoformat()
    if isinstance(valor, UUID):
        return str(valor)
    return valor


def _linha(modelo: object, colunas: tuple[str, ...]) -> dict[str, object]:
    return {c: _json(getattr(modelo, c)) for c in colunas}


class DadosTitularSql:
    def __init__(self, sessoes: async_sessionmaker[AsyncSession]) -> None:
        self._sessoes = sessoes

    async def exportar(self, lead_id: UUID) -> Mapping[str, object]:
        async with self._sessoes() as sessao:
            lead = await sessao.get(LeadModel, lead_id)
            if lead is None:
                return {}
            conversas = (
                await sessao.scalars(select(ConversaModel).where(ConversaModel.lead_id == lead_id))
            ).all()
            ids_conversa = [c.id for c in conversas]
            mensagens = (
                await sessao.scalars(
                    select(MensagemModel)
                    .where(MensagemModel.conversa_id.in_(ids_conversa))
                    .order_by(MensagemModel.criada_em)
                )
            ).all()
            eventos = (
                await sessao.scalars(
                    select(LeadEventoModel)
                    .where(LeadEventoModel.lead_id == lead_id)
                    .order_by(LeadEventoModel.seq)
                )
            ).all()
            agendamentos = (
                await sessao.scalars(
                    select(AgendamentoModel).where(AgendamentoModel.lead_id == lead_id)
                )
            ).all()
            resumos = (
                await sessao.scalars(
                    select(ResumoHandoffModel).where(ResumoHandoffModel.lead_id == lead_id)
                )
            ).all()
            crm = await sessao.get(CrmRegistroModel, lead_id)
            followups = (
                await sessao.scalars(select(FollowUpModel).where(FollowUpModel.lead_id == lead_id))
            ).all()
        return {
            "lead": _linha(
                lead,
                (
                    "id",
                    "canal",
                    "remetente_id",
                    "nome",
                    "ficha_qualificacao",
                    "intencao_atual",
                    "score",
                    "classificacao",
                    "score_motivos",
                    "proxima_acao",
                    "qualificado_em",
                    "atendimento_estado",
                    "opt_out_em",
                    "criado_em",
                    "atualizado_em",
                ),
            ),
            "conversas": [
                _linha(c, ("id", "canal", "status", "iniciada_em", "atualizada_em"))
                for c in conversas
            ],
            "mensagens": [
                {
                    **_linha(m, ("conversa_id", "papel", "texto", "criada_em", "status")),
                    "responsavel": (m.metadados or {}).get("responsavel"),
                    "anexos": [
                        {"tipo": a.get("tipo")} for a in (m.metadados or {}).get("midias", [])
                    ],
                }
                for m in mensagens
            ],
            "eventos": [_linha(e, ("tipo", "payload", "ocorrido_em")) for e in eventos],
            "agendamentos": [
                _linha(a, ("tipo", "modalidade", "status", "inicio", "fim", "itens", "criado_em"))
                for a in agendamentos
            ],
            "resumos": [_linha(r, ("versao", "gerado_em", "titulo", "secoes")) for r in resumos],
            "crm": _linha(crm, ("crm_id", "dados", "atualizado_em")) if crm else None,
            "followups": [
                _linha(f, ("tipo", "etapa", "status", "executar_em", "motivo")) for f in followups
            ],
        }

    async def anonimizar(self, lead_id: UUID, remetente_anonimo: str) -> Mapping[str, int]:
        async with self._sessoes.begin() as sessao:
            leads = await sessao.execute(
                update(LeadModel)
                .where(LeadModel.id == lead_id)
                .values(remetente_id=remetente_anonimo, nome=None, ficha_qualificacao={})
            )
            ids_conversa = select(ConversaModel.id).where(ConversaModel.lead_id == lead_id)
            metadados: Any = MensagemModel.metadados
            for chave in METADADOS_PESSOAIS:
                metadados = metadados.op("-")(chave)
            mensagens = await sessao.execute(
                update(MensagemModel)
                .where(MensagemModel.conversa_id.in_(ids_conversa))
                .values(texto=TEXTO_ANONIMIZADO, metadados=metadados)
            )
            payload: Any = LeadEventoModel.payload
            for chave in PAYLOAD_PESSOAL:
                payload = payload.op("-")(chave)
            eventos = await sessao.execute(
                update(LeadEventoModel)
                .where(LeadEventoModel.lead_id == lead_id)
                .values(payload=payload)
            )
            resumos = await sessao.execute(
                delete(ResumoHandoffModel).where(ResumoHandoffModel.lead_id == lead_id)
            )
            crm = await sessao.execute(
                delete(CrmRegistroModel).where(CrmRegistroModel.lead_id == lead_id)
            )
        return {
            "leads": _contagem(leads),
            "mensagens": _contagem(mensagens),
            "eventos": _contagem(eventos),
            "resumos": _contagem(resumos),
            "crm": _contagem(crm),
        }


def _contagem(resultado: object) -> int:
    return int(getattr(resultado, "rowcount", 0) or 0)
