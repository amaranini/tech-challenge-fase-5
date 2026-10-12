"""Proteção das rotas HTTP (ADR 015): chave de admin, rate limit e IP do cliente.

- Rotas administrativas e de atendimento (painel, leads, fila, agenda, LGPD, demo) exigem
  `X-API-Key` com uma das ADMIN_API_KEYS. Sem chave configurada: aberto só em ambiente
  local (aviso no start); em produção o bootstrap recusa subir.
- Chat público e webhooks: rate limit por IP e por remetente (LimitadorTaxaPort).
- Webhooks continuam protegidos pela assinatura do provedor.
"""

import hmac
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field

from fastapi import HTTPException, Request

from sdr.core.application.ports.limite_taxa import LimitadorTaxaPort
from sdr.core.domain.pii import mascarar_pii

logger = logging.getLogger(__name__)

HEADER_CHAVE = "X-API-Key"


class _SemLimite:
    async def consumir(self, chave: str, limite_por_minuto: int) -> float:
        return 0.0


@dataclass(frozen=True)
class ProtecaoHttp:
    chaves_admin: Sequence[str] = ()
    producao: bool = False
    confiar_proxy: bool = False
    limitador: LimitadorTaxaPort = field(default_factory=_SemLimite)
    limite_remetente_por_minuto: int = 20
    limite_ip_por_minuto: int = 60
    limite_ip_webhook_por_minuto: int = 600

    @property
    def aberta(self) -> bool:
        """Sem chave configurada fora de produção: rotas administrativas abertas (dev)."""
        return not self.chaves_admin and not self.producao

    def chave_valida(self, chave: str | None) -> bool:
        if not chave:
            return False
        recebida = chave.encode()
        return any(hmac.compare_digest(recebida, c.encode()) for c in self.chaves_admin)


def obter_protecao(request: Request) -> ProtecaoHttp:
    protecao: ProtecaoHttp = request.app.state.dependencias.protecao
    return protecao


def eh_admin(request: Request) -> bool:
    protecao = obter_protecao(request)
    return protecao.aberta or protecao.chave_valida(request.headers.get(HEADER_CHAVE))


async def exigir_admin(request: Request) -> None:
    """Dependência das rotas administrativas/de atendimento."""
    if not eh_admin(request):
        raise HTTPException(
            401, "credencial ausente ou inválida", headers={"WWW-Authenticate": HEADER_CHAVE}
        )


def ip_cliente(request: Request) -> str:
    if obter_protecao(request).confiar_proxy:
        encaminhado = request.headers.get("X-Forwarded-For", "")
        if primeiro := encaminhado.split(",")[0].strip():
            return primeiro
    return request.client.host if request.client else "desconhecido"


async def excedeu(request: Request, chave: str, limite_por_minuto: int) -> float:
    """Segundos de espera se a chave estourou o limite (0 = segue)."""
    espera = await obter_protecao(request).limitador.consumir(chave, limite_por_minuto)
    if espera > 0:
        logger.warning("Rate limit excedido (%s)", mascarar_pii(chave))
    return espera


async def limitar(request: Request, chave: str, limite_por_minuto: int) -> None:
    """Rate limit que responde 429 (chat público)."""
    if (espera := await excedeu(request, chave, limite_por_minuto)) > 0:
        raise HTTPException(
            429,
            "muitas mensagens em pouco tempo; tente de novo em instantes",
            headers={"Retry-After": str(max(1, round(espera)))},
        )
