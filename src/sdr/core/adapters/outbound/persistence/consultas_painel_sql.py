"""ConsultasPainelPort em SQL: agregações do dashboard direto no Postgres (só leitura).

Lê lead_eventos (a trilha de tudo o que aconteceu), leads (projeções da qualificação e do
atendimento), mensagens, agendamentos e followups_agendados. Nenhuma escrita aqui.
"""

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sdr.core.application.consultas.painel import (
    Contagem,
    DistribuicoesCoorte,
    EtapaFunil,
    EventoFollowUp,
    FiltroLeads,
    FollowUpPendente,
    LeadNoFunil,
    LeadPainel,
    PainelFollowUps,
    Periodo,
)
from sdr.core.domain.eventos import TipoEvento

COORTE = text(
    """
    WITH coorte AS (
        SELECT id, criado_em, intencao_atual, qualificado_em FROM leads
        WHERE criado_em >= :inicio AND criado_em < :fim
    ),
    msgs AS (
        SELECT c.lead_id,
               min(m.criada_em) FILTER (WHERE m.papel = 'lead') AS primeira_lead,
               min(m.criada_em) FILTER (WHERE m.papel = 'assistente') AS primeira_resposta
        FROM mensagens m JOIN conversas c ON c.id = m.conversa_id
        WHERE c.lead_id IN (SELECT id FROM coorte)
        GROUP BY c.lead_id
    ),
    evs AS (
        SELECT lead_id,
               bool_or(tipo = :intencao) AS teve_intencao,
               min(ocorrido_em) FILTER (WHERE tipo = :qualificado) AS qualificou_em,
               bool_or(tipo = :agendou) AS agendou
        FROM lead_eventos WHERE lead_id IN (SELECT id FROM coorte)
        GROUP BY lead_id
    )
    SELECT co.id, co.criado_em, co.intencao_atual, co.qualificado_em,
           coalesce(evs.teve_intencao, false) AS teve_intencao,
           coalesce(evs.qualificou_em, co.qualificado_em) AS qualificou_em,
           coalesce(evs.agendou, false)
             OR EXISTS (SELECT 1 FROM agendamentos a WHERE a.lead_id = co.id) AS agendou,
           msgs.primeira_lead, msgs.primeira_resposta
    FROM coorte co
    LEFT JOIN msgs ON msgs.lead_id = co.id
    LEFT JOIN evs ON evs.lead_id = co.id
    """
)

DISTRIBUICAO = """
    SELECT {expressao} AS chave, count(*) AS total FROM leads
    WHERE criado_em >= :inicio AND criado_em < :fim
    GROUP BY 1 ORDER BY 2 DESC, 1
"""
DIMENSOES = {
    "intencao": "coalesce(intencao_atual, 'indefinida')",
    "classificacao": "coalesce(classificacao, 'sem score')",
    "canal": "canal",
    "estado": "atendimento_estado",
}

EVENTOS_NO_PERIODO = text(
    """
    SELECT tipo, count(DISTINCT lead_id) AS leads, count(*) AS total,
           count(*) FILTER (WHERE payload->>'requer_template' = 'true') AS por_template
    FROM lead_eventos
    WHERE ocorrido_em >= :inicio AND ocorrido_em < :fim AND tipo = ANY(:tipos)
    GROUP BY tipo
    """
)

LEADS = """
    SELECT l.id, l.canal, l.remetente_id, l.nome, l.intencao_atual, l.score, l.classificacao,
           l.atendimento_estado, l.proxima_acao, l.criado_em, l.opt_out_em IS NOT NULL AS opt_out,
           l.demo,
           (SELECT max(m.criada_em) FROM mensagens m JOIN conversas c ON c.id = m.conversa_id
             WHERE c.lead_id = l.id) AS ultima_interacao_em,
           (SELECT count(*) FROM mensagens m JOIN conversas c ON c.id = m.conversa_id
             WHERE c.lead_id = l.id) AS total_mensagens,
           EXISTS (SELECT 1 FROM agendamentos a
                    WHERE a.lead_id = l.id AND a.status = 'ativo') AS agendado
    FROM leads l
    WHERE {condicoes}
    ORDER BY ultima_interacao_em DESC NULLS LAST, l.criado_em DESC
    LIMIT :limite
"""

PENDENTES = text(
    """
    SELECT f.lead_id, l.canal, l.remetente_id, f.tipo, f.etapa, f.executar_em, f.status
    FROM followups_agendados f JOIN leads l ON l.id = f.lead_id
    WHERE f.status IN ('pendente', 'processando')
    ORDER BY f.executar_em LIMIT 200
    """
)

RECENTES = text(
    """
    SELECT e.lead_id, l.canal, l.remetente_id, e.tipo, e.ocorrido_em, e.payload
    FROM lead_eventos e JOIN leads l ON l.id = e.lead_id
    WHERE e.ocorrido_em >= :inicio AND e.ocorrido_em < :fim AND e.tipo = ANY(:tipos)
    ORDER BY e.ocorrido_em DESC LIMIT :limite
    """
)

TIPOS_FOLLOWUP = [
    TipoEvento.FOLLOWUP_ENVIADO.value,
    TipoEvento.LEAD_REENGAJADO.value,
    TipoEvento.LEAD_OPT_OUT.value,
    TipoEvento.LEAD_ENCERRADO_INATIVIDADE.value,
]


def _segundos(depois: Any, antes: Any) -> float | None:
    if depois is None or antes is None or depois < antes:
        return None
    return float((depois - antes).total_seconds())


def _detalhe(tipo: str, payload: dict[str, Any]) -> str | None:
    if tipo == TipoEvento.FOLLOWUP_ENVIADO.value:
        partes = [str(payload.get("tipo", ""))]
        if payload.get("etapa"):
            partes.append(f"etapa {payload['etapa']}")
        if payload.get("requer_template"):
            partes.append("por template")
        if payload.get("entrega") == "falhou":
            partes.append("falhou")
        return ", ".join(p for p in partes if p)
    if tipo == TipoEvento.LEAD_REENGAJADO.value and payload.get("etapa"):
        return f"respondeu à etapa {payload['etapa']}"
    return None


class ConsultasPainelSql:
    def __init__(self, sessoes: async_sessionmaker[AsyncSession]) -> None:
        self._sessoes = sessoes

    @staticmethod
    def _janela(periodo: Periodo) -> dict[str, Any]:
        return {"inicio": periodo.inicio, "fim": periodo.fim}

    async def coorte(self, periodo: Periodo) -> list[LeadNoFunil]:
        async with self._sessoes() as sessao:
            linhas = (
                await sessao.execute(
                    COORTE,
                    {
                        **self._janela(periodo),
                        "intencao": TipoEvento.INTENCAO_IDENTIFICADA.value,
                        "qualificado": TipoEvento.LEAD_QUALIFICADO.value,
                        "agendou": TipoEvento.AGENDAMENTO_CRIADO.value,
                    },
                )
            ).mappings()
            resultado = []
            for r in linhas:
                if r["agendou"]:
                    etapa = EtapaFunil.AGENDADO
                elif r["qualificou_em"] is not None:
                    etapa = EtapaFunil.QUALIFICADO
                elif r["teve_intencao"] or r["intencao_atual"]:
                    etapa = EtapaFunil.COM_INTENCAO
                else:
                    etapa = EtapaFunil.NOVO
                resultado.append(
                    LeadNoFunil(
                        r["id"],
                        etapa,
                        _segundos(r["primeira_resposta"], r["primeira_lead"]),
                        _segundos(r["qualificou_em"], r["criado_em"]),
                    )
                )
        return resultado

    async def distribuicoes(self, periodo: Periodo) -> DistribuicoesCoorte:
        janela = self._janela(periodo)
        async with self._sessoes() as sessao:
            por: dict[str, tuple[Contagem, ...]] = {}
            for nome, expressao in DIMENSOES.items():
                linhas = await sessao.execute(
                    text(DISTRIBUICAO.format(expressao=expressao)), janela
                )
                por[nome] = tuple(Contagem(str(c), int(t)) for c, t in linhas)
            eventos: dict[str, dict[str, Any]] = {
                r["tipo"]: dict(r)
                for r in (
                    await sessao.execute(
                        EVENTOS_NO_PERIODO,
                        {
                            **janela,
                            "tipos": [
                                TipoEvento.HANDOFF_CONFIRMADO.value,
                                TipoEvento.HANDOFF_SLA_EXCEDIDO.value,
                            ],
                        },
                    )
                ).mappings()
            }
        return DistribuicoesCoorte(
            por_intencao=por["intencao"],
            por_classificacao=por["classificacao"],
            por_canal=por["canal"],
            por_estado_atendimento=por["estado"],
            handoffs=int(eventos.get(TipoEvento.HANDOFF_CONFIRMADO.value, {}).get("leads", 0)),
            sla_excedidos=int(
                eventos.get(TipoEvento.HANDOFF_SLA_EXCEDIDO.value, {}).get("total", 0)
            ),
        )

    async def leads(self, filtro: FiltroLeads) -> list[LeadPainel]:
        condicoes = ["true"]
        parametros: dict[str, Any] = {"limite": max(1, min(filtro.limite, 500))}
        if filtro.periodo is not None:
            condicoes.append("l.criado_em >= :inicio AND l.criado_em < :fim")
            parametros |= self._janela(filtro.periodo)
        if filtro.canal:
            condicoes.append("l.canal = :canal")
            parametros["canal"] = filtro.canal
        if filtro.intencao == "indefinida":
            condicoes.append("l.intencao_atual IS NULL")
        elif filtro.intencao:
            condicoes.append("l.intencao_atual = :intencao")
            parametros["intencao"] = filtro.intencao
        if filtro.estado_atendimento:
            condicoes.append("l.atendimento_estado = :estado")
            parametros["estado"] = filtro.estado_atendimento
        if filtro.classificacao:
            condicoes.append("l.classificacao = :classificacao")
            parametros["classificacao"] = filtro.classificacao
        if filtro.score_min is not None:
            condicoes.append("l.score >= :score_min")
            parametros["score_min"] = filtro.score_min
        if filtro.score_max is not None:
            condicoes.append("l.score <= :score_max")
            parametros["score_max"] = filtro.score_max
        if filtro.apenas_demo is not None:
            condicoes.append("l.demo = :demo")
            parametros["demo"] = filtro.apenas_demo
        consulta = text(LEADS.format(condicoes=" AND ".join(condicoes)))
        async with self._sessoes() as sessao:
            linhas = (await sessao.execute(consulta, parametros)).mappings()
            return [
                LeadPainel(
                    lead_id=r["id"],
                    canal=r["canal"],
                    remetente_id=r["remetente_id"],
                    nome=r["nome"],
                    intencao=r["intencao_atual"],
                    score=r["score"],
                    classificacao=r["classificacao"],
                    estado_atendimento=r["atendimento_estado"],
                    proxima_acao=r["proxima_acao"],
                    criado_em=r["criado_em"],
                    ultima_interacao_em=r["ultima_interacao_em"],
                    total_mensagens=int(r["total_mensagens"]),
                    agendado=bool(r["agendado"]),
                    opt_out=bool(r["opt_out"]),
                    demo=bool(r["demo"]),
                )
                for r in linhas
            ]

    async def followups(self, periodo: Periodo, limite_recentes: int = 50) -> PainelFollowUps:
        janela = self._janela(periodo)
        async with self._sessoes() as sessao:
            pendentes = [
                FollowUpPendente(**dict(r)) for r in (await sessao.execute(PENDENTES)).mappings()
            ]
            eventos: dict[str, dict[str, Any]] = {
                r["tipo"]: dict(r)
                for r in (
                    await sessao.execute(EVENTOS_NO_PERIODO, {**janela, "tipos": TIPOS_FOLLOWUP})
                ).mappings()
            }
            recentes = [
                EventoFollowUp(
                    r["lead_id"],
                    r["canal"],
                    r["remetente_id"],
                    r["tipo"],
                    r["ocorrido_em"],
                    _detalhe(r["tipo"], dict(r["payload"] or {})),
                )
                for r in (
                    await sessao.execute(
                        RECENTES, {**janela, "tipos": TIPOS_FOLLOWUP, "limite": limite_recentes}
                    )
                ).mappings()
            ]

        def total(tipo: TipoEvento, coluna: str = "total") -> int:
            return int(eventos.get(tipo.value, {}).get(coluna, 0))

        return PainelFollowUps(
            periodo=periodo,
            pendentes=tuple(pendentes),
            enviados=total(TipoEvento.FOLLOWUP_ENVIADO),
            enviados_por_template=total(TipoEvento.FOLLOWUP_ENVIADO, "por_template"),
            leads_com_followup=total(TipoEvento.FOLLOWUP_ENVIADO, "leads"),
            reengajados=total(TipoEvento.LEAD_REENGAJADO, "leads"),
            opt_outs=total(TipoEvento.LEAD_OPT_OUT, "leads"),
            encerrados_por_inatividade=total(TipoEvento.LEAD_ENCERRADO_INATIVIDADE, "leads"),
            recentes=tuple(recentes),
        )
