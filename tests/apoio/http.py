"""Monta `Dependencias` para testes de rota, só com os casos de uso que o teste usa."""

from typing import Any

from sdr.core.adapters.inbound.http.dependencias import Dependencias


def _nao_usado() -> Any:
    raise AssertionError("caso de uso não configurado neste teste")


def criar_dependencias(**fabricas: Any) -> Dependencias:
    padrao: dict[str, Any] = {
        "verificar_saude": _nao_usado,
        "processar_mensagem": _nao_usado,
        "obter_historico": _nao_usado,
        "listar_leads": _nao_usado,
    }
    return Dependencias(**(padrao | fabricas))
