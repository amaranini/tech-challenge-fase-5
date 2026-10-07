from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from sdr.core.domain.agente import RespostaAgente
from sdr.core.domain.conversa import Lead, Mensagem


@dataclass(frozen=True)
class EntradaAgente:
    lead: Lead
    historico: Sequence[Mensagem]  # mensagens anteriores da conversa, em ordem cronológica
    texto: str  # mensagem atual do lead
    instrucao_adicional: str | None = None  # ex.: correção pedida pelo caso de uso


class AgenteConversacionalPort(Protocol):
    async def responder(self, entrada: EntradaAgente) -> RespostaAgente: ...
