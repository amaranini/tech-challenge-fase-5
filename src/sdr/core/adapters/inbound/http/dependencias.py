from collections.abc import Callable
from dataclasses import dataclass

from fastapi import Request

from sdr.core.application.use_cases.verificar_saude import VerificarSaude


@dataclass(frozen=True)
class Dependencias:
    """Fábricas dos casos de uso do core, injetadas pelo composition root (bootstrap).

    Rotas das verticais recebem suas dependências ao serem criadas pelo VerticalPack.
    """

    verificar_saude: Callable[[], VerificarSaude]


def obter_dependencias(request: Request) -> Dependencias:
    dependencias: Dependencias = request.app.state.dependencias
    return dependencias


def obter_verificar_saude(request: Request) -> VerificarSaude:
    return obter_dependencias(request).verificar_saude()
