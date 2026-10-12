"""Qualidade da IA: métricas ONLINE (das conversas reais, a partir de lead_eventos e das
mensagens) e o resultado do último eval OFFLINE (roteiros de evals/, gravados pelo runner).

Só leitura, como o painel (ADR 014). Cada conversa pertence à VERSÃO do agente com que
começou: persona (prompt da vertical) · modelo · prompts do core — gravados em cada
resposta. Assim dá para comparar versões lado a lado.

Definições:
- pediu humano / opt-out / qualificou / agendou: leads da coorte com o evento;
- abandono: a última fala é da IA, o lead não respondeu há `abandono_horas` e a conversa
  não terminou bem (sem opt-out, sem agendamento ativo, sem pessoa da equipe atendendo).
  "Onde parou" = o que a última resposta pedia: o próximo campo da ficha, a escolha de
  horário, a descoberta do objetivo...;
- turnos até qualificar: respostas da IA até o evento LeadQualificado;
- latência: resposta da IA − última fala do lead antes dela (inclui o debounce);
- custo: tokens de cada resposta × preço do modelo (US$ por 1M tokens).
"""

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol
from uuid import UUID

from sdr.core.application.consultas.painel import Periodo

ABANDONO_HORAS_PADRAO = 24.0
SEM_VERSAO = "sem versão registrada"


@dataclass(frozen=True)
class VersaoAgente:
    """Chave de agrupamento: o que mudou entre uma versão e outra do agente."""

    persona: str
    modelo: str
    prompts_core: str  # lista de versões dos prompts do core ("" = conversa antiga)

    @property
    def chave(self) -> str:
        return f"{self.persona} · {self.modelo} · core#{self.hash_core}"

    @property
    def hash_core(self) -> str:
        if not self.prompts_core:
            return "antigo"
        return hashlib.sha1(self.prompts_core.encode()).hexdigest()[:6]


@dataclass(frozen=True)
class TokensPorModelo:
    modelo: str
    entrada: int
    saida: int


@dataclass(frozen=True)
class LinhaVersao:
    """O que o SQL devolve por versão (sem custo: o preço é aplicado aqui)."""

    versao: VersaoAgente
    conversas: int
    pediram_humano: int
    opt_outs: int
    qualificados: int
    agendamentos: int
    respostas: int
    respostas_reprovadas: int
    respostas_bloqueadas: int  # reprovadas duas vezes: saiu a resposta segura
    tentativas_injecao: int
    abandonos: int
    turnos_ate_qualificar_medio: float | None
    segundos_ate_qualificar_medio: float | None
    latencia_p50_s: float | None
    latencia_p95_s: float | None
    tokens: tuple[TokensPorModelo, ...]


@dataclass(frozen=True)
class PontoAbandono:
    versao: str
    ponto: str  # campo da ficha, "escolha de horário", "descoberta"...
    total: int


@dataclass(frozen=True)
class Incidente:
    """Tentativa de injeção ou resposta reprovada, com o link para o rastro."""

    tipo: str
    ocorrido_em: datetime
    lead_id: UUID
    lead_publico: str
    detalhe: str
    trace_url: str | None


@dataclass(frozen=True)
class DadosOnline:
    linhas: tuple[LinhaVersao, ...]
    abandonos: tuple[PontoAbandono, ...]
    incidentes: tuple[Incidente, ...]
    langfuse_projeto_url: str | None
    resumos_avaliados: int = 0
    resumos_uteis: int = 0


@dataclass(frozen=True)
class MetricasVersao:
    versao: str
    persona: str
    modelo: str
    prompts_core: str
    conversas: int
    taxa_pedido_humano: float | None
    taxa_opt_out: float | None
    taxa_abandono: float | None
    taxa_qualificacao: float | None
    turnos_ate_qualificar_medio: float | None
    segundos_ate_qualificar_medio: float | None
    respostas: int
    respostas_reprovadas: int
    respostas_bloqueadas: int
    tentativas_injecao: int
    latencia_p50_s: float | None
    latencia_p95_s: float | None
    custo_total_usd: float | None
    custo_por_conversa_usd: float | None
    custo_por_agendamento_usd: float | None
    agendamentos: int


@dataclass(frozen=True)
class QualidadeOnline:
    periodo: Periodo
    abandono_horas: float
    versoes: tuple[MetricasVersao, ...]  # mais conversas primeiro
    abandonos: tuple[PontoAbandono, ...]
    incidentes: tuple[Incidente, ...]
    langfuse_projeto_url: str | None
    resumos_avaliados: int
    taxa_resumo_util: float | None


@dataclass(frozen=True)
class AvaliacaoOffline:
    """Resumo de uma rodada do runner (evals/), gravado para a API ler."""

    id: UUID
    executada_em: datetime
    versao: Mapping[str, object]
    metricas: Mapping[str, object]
    contexto: Mapping[str, object] = field(default_factory=dict)


class ConsultasQualidadePort(Protocol):
    async def online(
        self, periodo: Periodo, abandono_horas: float, agora: datetime, apenas_reais: bool
    ) -> DadosOnline: ...


class AvaliacoesOfflineRepository(Protocol):
    async def salvar(self, avaliacao: AvaliacaoOffline) -> None: ...

    async def recentes(self, limite: int) -> list[AvaliacaoOffline]: ...


def _taxa(parte: int, todo: int) -> float | None:
    return parte / todo if todo else None


def custo_usd(
    tokens: Sequence[TokensPorModelo], precos: Mapping[str, Sequence[float]]
) -> float | None:
    """Preço pelo modelo; casa por prefixo ("gpt-4.1-mini-2025-04-14" → "gpt-4.1-mini").
    None se nenhum modelo tiver preço conhecido."""
    total, conhecido = 0.0, False
    for t in tokens:
        candidatos = sorted((k for k in precos if t.modelo.startswith(k)), key=len, reverse=True)
        if not candidatos:
            continue
        entrada, saida = precos[candidatos[0]]
        total += (t.entrada * entrada + t.saida * saida) / 1_000_000
        conhecido = True
    return total if conhecido else None


def metricas_da_versao(linha: LinhaVersao, precos: Mapping[str, Sequence[float]]) -> MetricasVersao:
    custo = custo_usd(linha.tokens, precos)
    v = linha.versao
    return MetricasVersao(
        versao=v.chave,
        persona=v.persona,
        modelo=v.modelo,
        prompts_core=v.prompts_core,
        conversas=linha.conversas,
        taxa_pedido_humano=_taxa(linha.pediram_humano, linha.conversas),
        taxa_opt_out=_taxa(linha.opt_outs, linha.conversas),
        taxa_abandono=_taxa(linha.abandonos, linha.conversas),
        taxa_qualificacao=_taxa(linha.qualificados, linha.conversas),
        turnos_ate_qualificar_medio=linha.turnos_ate_qualificar_medio,
        segundos_ate_qualificar_medio=linha.segundos_ate_qualificar_medio,
        respostas=linha.respostas,
        respostas_reprovadas=linha.respostas_reprovadas,
        respostas_bloqueadas=linha.respostas_bloqueadas,
        tentativas_injecao=linha.tentativas_injecao,
        latencia_p50_s=linha.latencia_p50_s,
        latencia_p95_s=linha.latencia_p95_s,
        custo_total_usd=custo,
        custo_por_conversa_usd=custo / linha.conversas
        if custo is not None and linha.conversas
        else None,
        custo_por_agendamento_usd=(
            custo / linha.agendamentos if custo is not None and linha.agendamentos else None
        ),
        agendamentos=linha.agendamentos,
    )


class ConsultarQualidade:
    def __init__(
        self,
        consultas: ConsultasQualidadePort,
        avaliacoes: AvaliacoesOfflineRepository,
        precos: Mapping[str, Sequence[float]],
    ) -> None:
        self._consultas = consultas
        self._avaliacoes = avaliacoes
        self._precos = precos

    async def online(
        self,
        periodo: Periodo,
        agora: datetime,
        *,
        abandono_horas: float = ABANDONO_HORAS_PADRAO,
        apenas_reais: bool = False,
    ) -> QualidadeOnline:
        if abandono_horas <= 0:
            raise ValueError("abandono_horas precisa ser positivo")
        dados = await self._consultas.online(periodo, abandono_horas, agora, apenas_reais)
        versoes = sorted(
            (metricas_da_versao(linha, self._precos) for linha in dados.linhas),
            key=lambda m: m.conversas,
            reverse=True,
        )
        return QualidadeOnline(
            periodo=periodo,
            abandono_horas=abandono_horas,
            versoes=tuple(versoes),
            abandonos=dados.abandonos,
            incidentes=dados.incidentes,
            langfuse_projeto_url=dados.langfuse_projeto_url,
            resumos_avaliados=dados.resumos_avaliados,
            taxa_resumo_util=_taxa(dados.resumos_uteis, dados.resumos_avaliados),
        )

    async def avaliacoes_offline(self, limite: int = 10) -> list[AvaliacaoOffline]:
        return await self._avaliacoes.recentes(limite)


class RegistrarAvaliacaoOffline:
    """Comando: o runner (evals/) grava o resumo de cada rodada."""

    def __init__(self, avaliacoes: AvaliacoesOfflineRepository) -> None:
        self._avaliacoes = avaliacoes

    async def executar(self, avaliacao: AvaliacaoOffline) -> AvaliacaoOffline:
        await self._avaliacoes.salvar(avaliacao)
        return avaliacao
