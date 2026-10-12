"""Qualidade da IA em SQL (só leitura) e o repositório das rodadas do eval offline.

Cada conversa pertence à versão do agente da sua PRIMEIRA resposta (persona · modelo ·
prompts do core, gravados nos metadados de cada mensagem). As agregações por versão
rodam no banco; o preço dos tokens é aplicado no caso de uso.
"""

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sdr.core.adapters.outbound.persistence.modelos import AvaliacaoOfflineModel
from sdr.core.application.consultas.painel import Periodo
from sdr.core.application.consultas.qualidade import (
    SEM_VERSAO,
    AvaliacaoOffline,
    DadosOnline,
    Incidente,
    LinhaVersao,
    PontoAbandono,
    TokensPorModelo,
    VersaoAgente,
)
from sdr.core.domain.eventos import TipoEvento

# Coorte + mensagens com a anterior (latência) + versão de cada conversa.
BASE = """
WITH coorte AS (
    SELECT id, canal, remetente_id, criado_em, atendimento_estado FROM leads
    WHERE criado_em >= :inicio AND criado_em < :fim AND (NOT :apenas_reais OR NOT demo)
),
msgs AS (
    SELECT c.lead_id, m.papel, m.criada_em, m.metadados,
           lag(m.papel) OVER w AS papel_anterior, lag(m.criada_em) OVER w AS anterior_em
    FROM mensagens m JOIN conversas c ON c.id = m.conversa_id
    WHERE c.lead_id IN (SELECT id FROM coorte)
    WINDOW w AS (PARTITION BY m.conversa_id ORDER BY m.criada_em)
),
primeira AS (
    SELECT DISTINCT ON (lead_id) lead_id,
           metadados->>'prompt_versao' AS persona,
           coalesce(metadados->>'modelo', '?') AS modelo,
           coalesce(metadados->'agente'->>'prompts', '') AS prompts
    FROM msgs WHERE papel = 'assistente' AND metadados ? 'prompt_versao'
    ORDER BY lead_id, criada_em
),
versao AS (
    SELECT co.id AS lead_id, coalesce(p.persona, :sem_versao) AS persona,
           coalesce(p.modelo, '?') AS modelo, coalesce(p.prompts, '') AS prompts
    FROM coorte co LEFT JOIN primeira p ON p.lead_id = co.id
)
"""

POR_VERSAO = text(
    BASE
    + """,
evs AS (
    SELECT lead_id,
           bool_or(tipo = 'HandoffSolicitado') AS humano,
           bool_or(tipo = 'LeadOptOut') AS opt_out,
           min(ocorrido_em) FILTER (WHERE tipo = 'LeadQualificado') AS qualificou_em,
           count(*) FILTER (WHERE tipo = 'AgendamentoCriado') AS agendamentos,
           count(*) FILTER (WHERE tipo = 'RespostaReprovadaNaValidacao') AS reprovadas,
           count(*) FILTER (WHERE tipo = 'RespostaReprovadaNaValidacao'
                            AND payload->>'fallback' = 'true') AS bloqueadas,
           count(*) FILTER (WHERE tipo = 'TentativaInjecaoDetectada') AS injecoes
    FROM lead_eventos WHERE lead_id IN (SELECT id FROM coorte) GROUP BY lead_id
),
ultima AS (
    SELECT DISTINCT ON (lead_id) lead_id, papel, criada_em FROM msgs
    ORDER BY lead_id, criada_em DESC
),
por_lead AS (
    SELECT co.id, v.persona, v.modelo, v.prompts, co.criado_em,
           coalesce(e.humano, false) AS humano, coalesce(e.opt_out, false) AS opt_out,
           e.qualificou_em, coalesce(e.agendamentos, 0) AS agendamentos,
           coalesce(e.reprovadas, 0) AS reprovadas, coalesce(e.bloqueadas, 0) AS bloqueadas,
           coalesce(e.injecoes, 0) AS injecoes,
           (SELECT count(*) FROM msgs m WHERE m.lead_id = co.id AND m.papel = 'assistente')
             AS respostas,
           (SELECT count(*) FROM msgs m WHERE m.lead_id = co.id AND m.papel = 'assistente'
              AND e.qualificou_em IS NOT NULL AND m.criada_em <= e.qualificou_em)
             AS turnos_ate_qualificar,
           (u.papel = 'assistente' AND u.criada_em < :limite_abandono
            AND NOT coalesce(e.opt_out, false) AND co.atendimento_estado = 'atendimento_ia'
            AND NOT EXISTS (SELECT 1 FROM agendamentos a
                             WHERE a.lead_id = co.id AND a.status = 'ativo')) AS abandonou
    FROM coorte co JOIN versao v ON v.lead_id = co.id
    LEFT JOIN evs e ON e.lead_id = co.id
    LEFT JOIN ultima u ON u.lead_id = co.id
)
SELECT persona, modelo, prompts, count(*) AS conversas,
       count(*) FILTER (WHERE humano) AS pediram_humano,
       count(*) FILTER (WHERE opt_out) AS opt_outs,
       count(*) FILTER (WHERE qualificou_em IS NOT NULL) AS qualificados,
       sum(agendamentos) AS agendamentos, sum(respostas) AS respostas,
       sum(reprovadas) AS reprovadas, sum(bloqueadas) AS bloqueadas,
       sum(injecoes) AS injecoes,
       count(*) FILTER (WHERE abandonou) AS abandonos,
       avg(turnos_ate_qualificar) FILTER (WHERE qualificou_em IS NOT NULL) AS turnos_qualificar,
       avg(extract(epoch FROM qualificou_em - criado_em))
         FILTER (WHERE qualificou_em IS NOT NULL) AS segundos_qualificar
FROM por_lead GROUP BY persona, modelo, prompts
"""
)

LATENCIA = text(
    BASE
    + """
SELECT v.persona, v.modelo, v.prompts,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM m.criada_em - m.anterior_em))
         AS p50,
       percentile_cont(0.95) WITHIN GROUP (ORDER BY extract(epoch FROM m.criada_em - m.anterior_em))
         AS p95
FROM msgs m JOIN versao v ON v.lead_id = m.lead_id
WHERE m.papel = 'assistente' AND m.papel_anterior = 'lead'
GROUP BY v.persona, v.modelo, v.prompts
"""
)

TOKENS = text(
    BASE
    + """
SELECT v.persona, v.modelo, v.prompts, m.metadados->>'modelo' AS modelo_resposta,
       sum(coalesce((m.metadados->'tokens'->>'entrada')::int, 0)) AS entrada,
       sum(coalesce((m.metadados->'tokens'->>'saida')::int, 0)) AS saida
FROM msgs m JOIN versao v ON v.lead_id = m.lead_id
WHERE m.papel = 'assistente' AND m.metadados ? 'tokens' AND m.metadados ? 'modelo'
GROUP BY v.persona, v.modelo, v.prompts, m.metadados->>'modelo'
"""
)

# Onde parou: o que a ÚLTIMA resposta ao lead pedia (campo da ficha, horário, objetivo...).
ABANDONOS = text(
    BASE
    + """,
ultima AS (
    SELECT DISTINCT ON (lead_id) lead_id, papel, criada_em FROM msgs
    ORDER BY lead_id, criada_em DESC
),
ultima_resposta AS (
    SELECT DISTINCT ON (lead_id) lead_id, metadados FROM msgs
    WHERE papel = 'assistente' AND metadados ? 'responde_a'
    ORDER BY lead_id, criada_em DESC
)
SELECT v.persona, v.modelo, v.prompts,
       CASE
         WHEN r.metadados->'agente'->>'no_resposta' = 'responder_agenda' THEN 'escolha de horário'
         WHEN r.metadados->'agente'->>'no_resposta' = 'descoberta' THEN 'descoberta do objetivo'
         WHEN r.metadados->'qualificacao'->>'proxima_acao' IS NOT NULL
           THEN 'próxima ação: ' || (r.metadados->'qualificacao'->>'proxima_acao')
         WHEN jsonb_array_length(coalesce(r.metadados->'qualificacao'->'campos_faltantes',
                                          '[]'::jsonb)) > 0
           THEN 'campo: ' || (r.metadados->'qualificacao'->'campos_faltantes'->>0)
         ELSE coalesce(r.metadados->'agente'->>'no_resposta', 'outro')
       END AS ponto,
       count(*) AS total
FROM coorte co
JOIN versao v ON v.lead_id = co.id
JOIN ultima u ON u.lead_id = co.id
LEFT JOIN ultima_resposta r ON r.lead_id = co.id
WHERE u.papel = 'assistente' AND u.criada_em < :limite_abandono
  AND co.atendimento_estado = 'atendimento_ia'
  AND NOT EXISTS (SELECT 1 FROM lead_eventos e WHERE e.lead_id = co.id AND e.tipo = 'LeadOptOut')
  AND NOT EXISTS (SELECT 1 FROM agendamentos a WHERE a.lead_id = co.id AND a.status = 'ativo')
GROUP BY 1, 2, 3, 4 ORDER BY total DESC
"""
)

INCIDENTES = text(
    """
SELECT e.tipo, e.ocorrido_em, e.payload, l.id AS lead_id, l.canal, l.remetente_id,
       (SELECT m.metadados->'trace'->>'url' FROM mensagens m
          JOIN conversas c ON c.id = m.conversa_id
         WHERE c.lead_id = e.lead_id AND m.papel = 'assistente' AND m.criada_em >= e.ocorrido_em
         ORDER BY m.criada_em LIMIT 1) AS trace_url
FROM lead_eventos e JOIN leads l ON l.id = e.lead_id
WHERE e.ocorrido_em >= :inicio AND e.ocorrido_em < :fim AND e.tipo = ANY(:tipos)
  AND (NOT :apenas_reais OR NOT l.demo)
ORDER BY e.ocorrido_em DESC LIMIT 30
"""
)

RESUMOS = text(
    """
SELECT count(*) AS avaliados, count(*) FILTER (WHERE payload->>'util' = 'true') AS uteis
FROM lead_eventos WHERE tipo = :tipo AND ocorrido_em >= :inicio AND ocorrido_em < :fim
"""
)

ULTIMO_TRACE = text(
    """
SELECT metadados->'trace'->>'url' FROM mensagens
WHERE metadados->'trace'->>'url' IS NOT NULL ORDER BY criada_em DESC LIMIT 1
"""
)


def _versao(r: Any) -> VersaoAgente:
    return VersaoAgente(r["persona"], r["modelo"], r["prompts"])


def _float(valor: Any) -> float | None:
    return float(valor) if valor is not None else None


def projeto_langfuse(trace_url: str | None) -> str | None:
    """https://…/project/<id>/traces/<trace> → https://…/project/<id>."""
    if not trace_url or "/traces/" not in trace_url:
        return None
    return trace_url.split("/traces/")[0]


def _publico(canal: str, remetente: str) -> str:
    return remetente if canal == "web" else f"{canal}:{remetente}"


def _detalhe(tipo: str, payload: dict[str, Any]) -> str:
    if tipo == TipoEvento.TENTATIVA_INJECAO.value:
        padroes = ", ".join(payload.get("padroes") or []) or "—"
        return f"origem {'+'.join(payload.get('origens') or [])}; padrões: {padroes}"
    partes = []
    if payload.get("codigos_invalidos"):
        partes.append(f"códigos inexistentes: {', '.join(payload['codigos_invalidos'])}")
    if payload.get("promessas"):
        partes.append(f"promessa: {', '.join(payload['promessas'])}")
    partes.append("resposta segura enviada" if payload.get("fallback") else "corrigida na 2ª")
    return "; ".join(partes)


class ConsultasQualidadeSql:
    def __init__(self, sessoes: async_sessionmaker[AsyncSession]) -> None:
        self._sessoes = sessoes

    async def online(
        self, periodo: Periodo, abandono_horas: float, agora: datetime, apenas_reais: bool
    ) -> DadosOnline:
        params: dict[str, Any] = {
            "inicio": periodo.inicio,
            "fim": periodo.fim,
            "apenas_reais": apenas_reais,
            "sem_versao": SEM_VERSAO,
            "limite_abandono": agora - timedelta(hours=abandono_horas),
        }
        async with self._sessoes() as sessao:
            base = (await sessao.execute(POR_VERSAO, params)).mappings().all()
            latencias = {_versao(r): r for r in (await sessao.execute(LATENCIA, params)).mappings()}
            tokens: dict[VersaoAgente, list[TokensPorModelo]] = {}
            for r in (await sessao.execute(TOKENS, params)).mappings():
                tokens.setdefault(_versao(r), []).append(
                    TokensPorModelo(r["modelo_resposta"], int(r["entrada"]), int(r["saida"]))
                )
            abandonos = tuple(
                PontoAbandono(_versao(r).chave, r["ponto"], int(r["total"]))
                for r in (await sessao.execute(ABANDONOS, params)).mappings()
            )
            incidentes = tuple(
                Incidente(
                    r["tipo"],
                    r["ocorrido_em"],
                    r["lead_id"],
                    _publico(r["canal"], r["remetente_id"]),
                    _detalhe(r["tipo"], dict(r["payload"] or {})),
                    r["trace_url"],
                )
                for r in (
                    await sessao.execute(
                        INCIDENTES,
                        params
                        | {
                            "tipos": [
                                TipoEvento.TENTATIVA_INJECAO.value,
                                TipoEvento.RESPOSTA_REPROVADA.value,
                            ]
                        },
                    )
                ).mappings()
            )
            resumos = (
                (await sessao.execute(RESUMOS, params | {"tipo": TipoEvento.RESUMO_AVALIADO.value}))
                .mappings()
                .one()
            )
            ultimo_trace = await sessao.scalar(ULTIMO_TRACE)
        linhas = []
        for r in base:
            v = _versao(r)
            lat = latencias.get(v)
            linhas.append(
                LinhaVersao(
                    versao=v,
                    conversas=int(r["conversas"]),
                    pediram_humano=int(r["pediram_humano"]),
                    opt_outs=int(r["opt_outs"]),
                    qualificados=int(r["qualificados"]),
                    agendamentos=int(r["agendamentos"] or 0),
                    respostas=int(r["respostas"] or 0),
                    respostas_reprovadas=int(r["reprovadas"] or 0),
                    respostas_bloqueadas=int(r["bloqueadas"] or 0),
                    tentativas_injecao=int(r["injecoes"] or 0),
                    abandonos=int(r["abandonos"]),
                    turnos_ate_qualificar_medio=_float(r["turnos_qualificar"]),
                    segundos_ate_qualificar_medio=_float(r["segundos_qualificar"]),
                    latencia_p50_s=_float(lat["p50"]) if lat else None,
                    latencia_p95_s=_float(lat["p95"]) if lat else None,
                    tokens=tuple(tokens.get(v, ())),
                )
            )
        return DadosOnline(
            linhas=tuple(linhas),
            abandonos=abandonos,
            incidentes=incidentes,
            langfuse_projeto_url=projeto_langfuse(ultimo_trace),
            resumos_avaliados=int(resumos["avaliados"]),
            resumos_uteis=int(resumos["uteis"]),
        )


class AvaliacoesOfflineSql:
    def __init__(self, sessoes: async_sessionmaker[AsyncSession]) -> None:
        self._sessoes = sessoes

    async def salvar(self, avaliacao: AvaliacaoOffline) -> None:
        async with self._sessoes.begin() as sessao:
            sessao.add(
                AvaliacaoOfflineModel(
                    id=avaliacao.id,
                    executada_em=avaliacao.executada_em,
                    versao=dict(avaliacao.versao),
                    metricas=dict(avaliacao.metricas),
                    contexto=dict(avaliacao.contexto),
                )
            )

    async def recentes(self, limite: int) -> list[AvaliacaoOffline]:
        async with self._sessoes() as sessao:
            modelos = (
                await sessao.scalars(
                    select(AvaliacaoOfflineModel)
                    .order_by(AvaliacaoOfflineModel.executada_em.desc())
                    .limit(limite)
                )
            ).all()
        return [
            AvaliacaoOffline(m.id, m.executada_em, m.versao, m.metricas, m.contexto)
            for m in modelos
        ]
