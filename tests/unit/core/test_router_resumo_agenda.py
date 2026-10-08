"""GET /leads/{lead_id}/resumo, GET /agendamentos e o agendamento no detalhe do lead."""

from datetime import datetime, timedelta
from uuid import uuid4

import httpx

from sdr.core.adapters.inbound.http.app import criar_app
from sdr.core.application.use_cases.consultar_agenda_e_resumo import (
    ListarAgendamentos,
    ObterResumo,
)
from sdr.core.application.use_cases.obter_lead import ObterLead
from sdr.core.domain.agenda import Agendamento, Responsavel, StatusAgendamento
from sdr.core.domain.conversa import Canal, Lead
from sdr.core.domain.eventos import TipoEvento
from tests.apoio.fakes import (
    FUSO_SP,
    AgendaFake,
    LeadEventoRepositoryFake,
    LeadRepositoryFake,
    RelogioFake,
)
from tests.apoio.http import criar_dependencias
from tests.unit.core.test_resumo_handoff import Cenario as CenarioResumo

AGORA = datetime(2026, 10, 5, 10, 0, tzinfo=FUSO_SP)
SOL = Responsavel(uuid4(), "Sol", "consultora")


def agendamento(lead: Lead, dias: int, status: StatusAgendamento) -> Agendamento:
    inicio = AGORA + timedelta(days=dias)
    return Agendamento(
        uuid4(), lead.id, SOL, uuid4(), inicio, inicio + timedelta(hours=1), "aula",
        "presencial", status, AGORA, ("P-1",),
    )  # fmt: skip


def cliente(**fabricas: object) -> httpx.AsyncClient:
    app = criar_app(criar_dependencias(**fabricas))
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


async def test_resumo_404_sem_lead_ou_sem_resumo_e_200_com_versoes() -> None:
    c = CenarioResumo({"perfil": "Quer treinar à noite."})
    obter = ObterResumo(c.leads, c.resumos)
    async with cliente(obter_resumo=lambda: obter) as http:
        assert (await http.get("/leads/ninguem/resumo")).status_code == 404
        sem = await http.get("/leads/lead-resumo/resumo")
        assert sem.status_code == 404
        assert "ainda não tem resumo" in sem.json()["detail"]

        await c.gerar.ao_publicar([c.evento(TipoEvento.LEAD_QUALIFICADO)])
        resposta = await http.get("/leads/lead-resumo/resumo")
        assert (await http.get("/leads/lead-resumo/resumo?versao=9")).status_code == 404

    corpo = resposta.json()
    assert resposta.status_code == 200
    assert (corpo["versao"], corpo["versoes"], corpo["gatilho"]) == (1, [1], "LeadQualificado")
    secoes = {s["chave"]: s for s in corpo["secoes"]}
    assert secoes["perfil"]["conteudo"] == "Quer treinar à noite."
    assert secoes["objecoes"]["conteudo"] == "não informado"
    assert secoes["ficha"]["tipo"] == "ficha"
    assert corpo["descartados"] == []


async def test_agendamentos_listados_com_lead_publico_e_filtro_de_status() -> None:
    leads = LeadRepositoryFake()
    lead = Lead.novo(Canal.WEB, "lead-agenda")
    await leads.salvar(lead)
    agenda = AgendaFake([SOL], [])
    ativo = agendamento(lead, 2, StatusAgendamento.ATIVO)
    cancelado = agendamento(lead, 1, StatusAgendamento.CANCELADO)
    agenda.agendamentos = {ativo.id: ativo, cancelado.id: cancelado}
    listar = ListarAgendamentos(agenda, leads)

    async with cliente(listar_agendamentos=lambda: listar) as http:
        todos = (await http.get("/agendamentos")).json()
        so_ativos = (await http.get("/agendamentos", params={"status": "ativo"})).json()

    assert [a["status"] for a in todos] == ["cancelado", "ativo"]  # por início
    assert [a["id"] for a in so_ativos] == [str(ativo.id)]
    assert so_ativos[0]["lead_id"] == "lead-agenda"
    assert so_ativos[0]["responsavel"]["nome"] == "Sol"
    assert so_ativos[0]["itens"] == ["P-1"]


async def test_detalhe_do_lead_traz_o_agendamento_ativo() -> None:
    leads = LeadRepositoryFake()
    lead = Lead.novo(Canal.WEB, "lead-agenda")
    await leads.salvar(lead)
    agenda = AgendaFake([SOL], [])
    ativo = agendamento(lead, 2, StatusAgendamento.ATIVO)
    agenda.agendamentos[ativo.id] = ativo
    obter = ObterLead(
        leads, LeadEventoRepositoryFake(), (), agenda=agenda, relogio=RelogioFake(AGORA)
    )

    async with cliente(obter_lead=lambda: obter) as http:
        corpo = (await http.get("/leads/lead-agenda")).json()

    assert corpo["agendamento"]["id"] == str(ativo.id)
    assert corpo["agendamento"]["modalidade"] == "presencial"
