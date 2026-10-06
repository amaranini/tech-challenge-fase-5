from collections.abc import Callable
from dataclasses import dataclass

from fastapi import Request

from sdr.application.use_cases.verificar_saude import VerificarSaude


@dataclass(frozen=True)
class Dependencias:
    """Fábricas de casos de uso injetadas pelo composition root (bootstrap)."""

    verificar_saude: Callable[[], VerificarSaude]


def obter_dependencias(request: Request) -> Dependencias:
    dependencias: Dependencias = request.app.state.dependencias
    return dependencias


def obter_verificar_saude(request: Request) -> VerificarSaude:
    return obter_dependencias(request).verificar_saude()
