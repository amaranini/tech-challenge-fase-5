"""Atendimento do lead: quem está conversando com ele — a IA ou uma pessoa da equipe.

Máquina de estados explícita (transição fora da tabela levanta erro):

    ATENDIMENTO_IA ──solicitar──▶ CONFIRMANDO_HANDOFF ──sim──▶ AGUARDANDO_HUMANO
          ▲                         │ não / ambígua 2x              │      │
          └─────────────────────────┘                               │      │ assumir
          ▲                                                         │      ▼
          │◀──── sim ── CONFIRMANDO_RETORNO_IA ◀── quer voltar ─────┘  ATENDIMENTO_HUMANO
          │               │ não / ambígua 2x ──▶ AGUARDANDO_HUMANO          │
          └───────────────────────────── devolver ─────────────────────────┘

Toda transição devolve (novo estado, eventos). O horário de atendimento é da OPERAÇÃO
(value object `HorarioAtendimento`), não da vertical.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta
from enum import StrEnum
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from sdr.core.domain.eventos import EventoLead, TipoEvento

DIAS_ABREVIADOS = ("seg", "ter", "qua", "qui", "sex", "sab", "dom")
NOMES_DIAS = ("segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo")


class EstadoAtendimento(StrEnum):
    ATENDIMENTO_IA = "atendimento_ia"
    CONFIRMANDO_HANDOFF = "confirmando_handoff"
    AGUARDANDO_HUMANO = "aguardando_humano"
    ATENDIMENTO_HUMANO = "atendimento_humano"
    CONFIRMANDO_RETORNO_IA = "confirmando_retorno_ia"


E = EstadoAtendimento
TRANSICOES: Mapping[EstadoAtendimento, frozenset[EstadoAtendimento]] = {
    E.ATENDIMENTO_IA: frozenset({E.CONFIRMANDO_HANDOFF}),
    E.CONFIRMANDO_HANDOFF: frozenset({E.AGUARDANDO_HUMANO, E.ATENDIMENTO_IA}),
    E.AGUARDANDO_HUMANO: frozenset({E.ATENDIMENTO_HUMANO, E.CONFIRMANDO_RETORNO_IA}),
    E.ATENDIMENTO_HUMANO: frozenset({E.ATENDIMENTO_IA}),
    E.CONFIRMANDO_RETORNO_IA: frozenset({E.ATENDIMENTO_IA, E.AGUARDANDO_HUMANO}),
}
NA_FILA = frozenset({E.AGUARDANDO_HUMANO, E.CONFIRMANDO_RETORNO_IA})


class MotivoHandoff(StrEnum):
    PEDIDO_EXPLICITO = "pedido_explicito"
    FRUSTRACAO = "frustracao"
    FORA_DO_ESCOPO = "fora_do_escopo"
    NEGOCIACAO = "negociacao"


class TransicaoInvalidaError(Exception):
    def __init__(self, de: EstadoAtendimento, para: EstadoAtendimento) -> None:
        super().__init__(f"transição inválida: {de.value} → {para.value}")
        self.de = de
        self.para = para


@dataclass(frozen=True)
class Atendimento:
    """Estado de atendimento de um lead (agregado). Métodos devolvem (novo, eventos)."""

    lead_id: UUID
    estado: EstadoAtendimento = EstadoAtendimento.ATENDIMENTO_IA
    desde: datetime | None = None  # entrada no estado atual
    na_fila_desde: datetime | None = None  # tempo de espera conta daqui
    motivo: MotivoHandoff | None = None
    responsavel: str | None = None  # quem assumiu (ATENDIMENTO_HUMANO)
    respostas_ambiguas: int = 0  # na confirmação em curso

    @property
    def na_fila(self) -> bool:
        return self.estado in NA_FILA

    def _evento(self, tipo: TipoEvento, momento: datetime, **payload: object) -> EventoLead:
        return EventoLead(self.lead_id, tipo, momento, payload)

    def _ir(self, destino: EstadoAtendimento, momento: datetime, **mudancas: Any) -> "Atendimento":
        if destino not in TRANSICOES[self.estado]:
            raise TransicaoInvalidaError(self.estado, destino)
        return replace(self, estado=destino, desde=momento, respostas_ambiguas=0, **mudancas)

    # ------------------------------------------------------------------ handoff
    def solicitar_handoff(
        self, motivo: MotivoHandoff, momento: datetime
    ) -> tuple["Atendimento", list[EventoLead]]:
        novo = self._ir(E.CONFIRMANDO_HANDOFF, momento, motivo=motivo)
        return novo, [self._evento(TipoEvento.HANDOFF_SOLICITADO, momento, motivo=motivo.value)]

    def responder_handoff(
        self, confirmado: bool | None, momento: datetime
    ) -> tuple["Atendimento", list[EventoLead]]:
        """confirmado=None: resposta ambígua — pergunta de novo UMA vez; na segunda, segue
        com a IA."""
        if self.estado is not E.CONFIRMANDO_HANDOFF:
            raise TransicaoInvalidaError(self.estado, E.AGUARDANDO_HUMANO)
        if confirmado:
            novo = self._ir(E.AGUARDANDO_HUMANO, momento, na_fila_desde=momento)
            motivo = self.motivo.value if self.motivo else None
            return novo, [self._evento(TipoEvento.HANDOFF_CONFIRMADO, momento, motivo=motivo)]
        if confirmado is None and self.respostas_ambiguas == 0:
            return replace(self, respostas_ambiguas=1), []
        novo = self._ir(E.ATENDIMENTO_IA, momento, motivo=None)
        motivo_recusa = "recusou" if confirmado is False else "resposta_ambigua"
        return novo, [self._evento(TipoEvento.HANDOFF_RECUSADO, momento, motivo=motivo_recusa)]

    # ------------------------------------------------------------------ fila
    def registrar_mensagem_na_espera(
        self, momento: datetime
    ) -> tuple["Atendimento", list[EventoLead]]:
        if self.estado is not E.AGUARDANDO_HUMANO:
            raise TransicaoInvalidaError(self.estado, E.AGUARDANDO_HUMANO)
        return self, [
            self._evento(
                TipoEvento.MENSAGEM_NA_ESPERA, momento, espera_segundos=self.espera(momento)
            )
        ]

    def espera(self, momento: datetime) -> int:
        if self.na_fila_desde is None:
            return 0
        return max(0, int((momento - self.na_fila_desde).total_seconds()))

    def assumir(
        self, responsavel: str, momento: datetime
    ) -> tuple["Atendimento", list[EventoLead]]:
        espera = self.espera(momento)
        novo = self._ir(E.ATENDIMENTO_HUMANO, momento, responsavel=responsavel)
        evento = self._evento(
            TipoEvento.ATENDIMENTO_HUMANO_INICIADO,
            momento,
            responsavel=responsavel,
            espera_segundos=espera,
        )
        return novo, [evento]

    def devolver(self, momento: datetime) -> tuple["Atendimento", list[EventoLead]]:
        # A tabela permite chegar em ATENDIMENTO_IA por outros caminhos (recusa, retorno):
        # devolver é só de quem está em atendimento humano.
        if self.estado is not E.ATENDIMENTO_HUMANO:
            raise TransicaoInvalidaError(self.estado, E.ATENDIMENTO_IA)
        responsavel = self.responsavel
        novo = self._ir(
            E.ATENDIMENTO_IA, momento, na_fila_desde=None, motivo=None, responsavel=None
        )
        evento = self._evento(
            TipoEvento.ATENDIMENTO_HUMANO_ENCERRADO, momento, responsavel=responsavel
        )
        return novo, [evento]

    # ------------------------------------------------------------------ retorno à IA
    def solicitar_retorno_ia(self, momento: datetime) -> tuple["Atendimento", list[EventoLead]]:
        novo = self._ir(E.CONFIRMANDO_RETORNO_IA, momento)
        return novo, [self._evento(TipoEvento.RETORNO_IA_SOLICITADO, momento)]

    def responder_retorno_ia(
        self, confirmado: bool | None, momento: datetime
    ) -> tuple["Atendimento", list[EventoLead]]:
        """Confirmar cancela a entrada na fila; desistir (ou ambígua 2x) volta a aguardar."""
        if self.estado is not E.CONFIRMANDO_RETORNO_IA:
            raise TransicaoInvalidaError(self.estado, E.ATENDIMENTO_IA)
        if confirmado:
            espera = self.espera(momento)
            novo = self._ir(E.ATENDIMENTO_IA, momento, na_fila_desde=None, motivo=None)
            evento = self._evento(TipoEvento.RETORNO_IA_CONFIRMADO, momento, espera_segundos=espera)
            return novo, [evento]
        if confirmado is None and self.respostas_ambiguas == 0:
            return replace(self, respostas_ambiguas=1), []
        return self._ir(E.AGUARDANDO_HUMANO, momento), []  # continua na fila (mesmo na_fila_desde)


# ---------------------------------------------------------------------- horário


@dataclass(frozen=True)
class HorarioAtendimento:
    """Quando a equipe atende (dias da semana + faixas de horário, no fuso da operação)."""

    dias: frozenset[int]  # 0 = segunda
    faixas: tuple[tuple[time, time], ...]
    fuso: ZoneInfo = field(default_factory=lambda: ZoneInfo("America/Sao_Paulo"))

    @classmethod
    def de_texto(cls, dias: str, faixas: str, fuso: str) -> "HorarioAtendimento":
        """dias: "seg-sex" ou "seg,qua,sex"; faixas: "09:00-12:00,13:00-18:00"."""
        indices: set[int] = set()
        for parte in (p.strip().casefold() for p in dias.split(",") if p.strip()):
            inicio, _, fim = parte.partition("-")
            a = DIAS_ABREVIADOS.index(inicio.strip()[:3])
            b = DIAS_ABREVIADOS.index(fim.strip()[:3]) if fim else a
            indices.update(range(a, b + 1))
        intervalos = []
        for parte in (p.strip() for p in faixas.split(",") if p.strip()):
            de, _, ate = parte.partition("-")
            intervalos.append((time.fromisoformat(de.strip()), time.fromisoformat(ate.strip())))
        if not indices or not intervalos:
            raise ValueError("horário de atendimento precisa de dias e faixas")
        return cls(frozenset(indices), tuple(sorted(intervalos)), ZoneInfo(fuso))

    def aberto(self, momento: datetime) -> bool:
        local = momento.astimezone(self.fuso)
        if local.weekday() not in self.dias:
            return False
        return any(de <= local.time() < ate for de, ate in self.faixas)

    def proxima_abertura(self, momento: datetime) -> datetime:
        """Próximo instante em que o atendimento abre (o próprio momento, se já aberto)."""
        if self.aberto(momento):
            return momento
        local = momento.astimezone(self.fuso)
        for dias in range(8):
            dia: date = local.date() + timedelta(days=dias)
            if dia.weekday() not in self.dias:
                continue
            for de, _ in self.faixas:
                abertura = datetime.combine(dia, de, self.fuso)
                if abertura > local:
                    return abertura
        raise ValueError("horário de atendimento sem abertura na próxima semana")

    def descrever(self) -> str:
        """Ex.: "de segunda a sexta, das 9h às 18h"."""
        dias = sorted(self.dias)
        if dias == list(range(dias[0], dias[-1] + 1)) and len(dias) > 1:
            texto_dias = f"de {NOMES_DIAS[dias[0]]} a {NOMES_DIAS[dias[-1]]}"
        else:
            texto_dias = ", ".join(NOMES_DIAS[d] for d in dias)
        faixas = " e ".join(f"das {_hora(de)} às {_hora(ate)}" for de, ate in self.faixas)
        return f"{texto_dias}, {faixas}"


def _hora(t: time) -> str:
    return f"{t.hour}h" if t.minute == 0 else f"{t.hour}h{t.minute:02d}"


def descrever_retorno(abertura: datetime, momento: datetime, fuso: ZoneInfo) -> str:
    """Quando a equipe volta, em relação a agora: "hoje às 13h", "amanhã às 9h",
    "segunda (12/10) às 9h"."""
    local, agora = abertura.astimezone(fuso), momento.astimezone(fuso)
    if local.date() == agora.date():
        dia = "hoje"
    elif local.date() == agora.date() + timedelta(days=1):
        dia = f"amanhã ({NOMES_DIAS[local.weekday()]})"
    else:
        dia = f"{NOMES_DIAS[local.weekday()]} ({local:%d/%m})"
    return f"{dia} às {_hora(local.time())}"


# ---------------------------------------------------------------------- ação do turno


class TipoAcaoAtendimento(StrEnum):
    SOLICITAR_HANDOFF = "solicitar_handoff"
    RESPONDER_HANDOFF = "responder_handoff"
    MENSAGEM_NA_ESPERA = "mensagem_na_espera"
    SOLICITAR_RETORNO_IA = "solicitar_retorno_ia"
    RESPONDER_RETORNO_IA = "responder_retorno_ia"


@dataclass(frozen=True)
class AcaoAtendimento:
    """O que a conversa decidiu sobre o atendimento neste turno. O grafo usa `aplicar` para
    prever o novo estado (e redigir a resposta); o turno aplica de verdade, com
    compare-and-set, só se o estado não mudou enquanto o grafo pensava."""

    tipo: TipoAcaoAtendimento
    confirmado: bool | None = None  # respostas: True/False; None = ambígua
    motivo: MotivoHandoff | None = None  # ao solicitar o handoff

    def aplicar(
        self, atendimento: Atendimento, momento: datetime
    ) -> tuple[Atendimento, list[EventoLead]]:
        match self.tipo:
            case TipoAcaoAtendimento.SOLICITAR_HANDOFF:
                motivo = self.motivo or MotivoHandoff.PEDIDO_EXPLICITO
                return atendimento.solicitar_handoff(motivo, momento)
            case TipoAcaoAtendimento.RESPONDER_HANDOFF:
                return atendimento.responder_handoff(self.confirmado, momento)
            case TipoAcaoAtendimento.MENSAGEM_NA_ESPERA:
                return atendimento.registrar_mensagem_na_espera(momento)
            case TipoAcaoAtendimento.SOLICITAR_RETORNO_IA:
                return atendimento.solicitar_retorno_ia(momento)
            case _:
                return atendimento.responder_retorno_ia(self.confirmado, momento)
