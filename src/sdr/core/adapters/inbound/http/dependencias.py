from collections.abc import Callable
from dataclasses import dataclass

from fastapi import Request

from sdr.core.application.use_cases.consultar_agenda_e_resumo import (
    ListarAgendamentos,
    ObterResumo,
)
from sdr.core.application.use_cases.consultar_conversas import ListarLeads, ObterHistorico
from sdr.core.application.use_cases.obter_lead import ObterLead
from sdr.core.application.use_cases.receber_mensagem import ReceberMensagem
from sdr.core.application.use_cases.verificar_saude import VerificarSaude


@dataclass(frozen=True)
class Dependencias:
    """Fábricas dos casos de uso do core, injetadas pelo composition root (bootstrap).

    Rotas das verticais recebem suas dependências ao serem criadas pelo VerticalPack.
    """

    verificar_saude: Callable[[], VerificarSaude]
    receber_mensagem: Callable[[], ReceberMensagem]
    obter_historico: Callable[[], ObterHistorico]
    listar_leads: Callable[[], ListarLeads]
    obter_lead: Callable[[], ObterLead]
    obter_resumo: Callable[[], ObterResumo]
    listar_agendamentos: Callable[[], ListarAgendamentos]


def obter_dependencias(request: Request) -> Dependencias:
    dependencias: Dependencias = request.app.state.dependencias
    return dependencias


def obter_verificar_saude(request: Request) -> VerificarSaude:
    return obter_dependencias(request).verificar_saude()


def obter_receber_mensagem(request: Request) -> ReceberMensagem:
    return obter_dependencias(request).receber_mensagem()


def obter_obter_historico(request: Request) -> ObterHistorico:
    return obter_dependencias(request).obter_historico()


def obter_listar_leads(request: Request) -> ListarLeads:
    return obter_dependencias(request).listar_leads()


def obter_obter_lead(request: Request) -> ObterLead:
    return obter_dependencias(request).obter_lead()


def obter_obter_resumo(request: Request) -> ObterResumo:
    return obter_dependencias(request).obter_resumo()


def obter_listar_agendamentos(request: Request) -> ListarAgendamentos:
    return obter_dependencias(request).listar_agendamentos()
