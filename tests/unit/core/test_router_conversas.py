import httpx
import pytest

from sdr.core.adapters.inbound.http.app import criar_app
from sdr.core.application.use_cases.consultar_conversas import ListarLeads, ObterHistorico
from sdr.core.application.use_cases.processar_turno import ProcessarTurno
from sdr.core.application.use_cases.receber_mensagem import ReceberMensagem
from sdr.core.domain.agente import Persona, RespostaAgente
from sdr.core.domain.conversa import Canal
from tests.apoio.fakes import (
    AgendadorFake,
    AgenteRoteirizado,
    CanalFake,
    CatalogoFake,
    ConversaRepositoryFake,
    LeadEventoRepositoryFake,
    LeadRepositoryFake,
    TravaFake,
    item,
)
from tests.apoio.http import criar_dependencias

PERSONA = Persona("Ana", "v1", "...", padrao_codigo_item=r"\bA-\d+\b")


class Api:
    def __init__(self, *respostas: RespostaAgente) -> None:
        leads, conversas, eventos = (
            LeadRepositoryFake(),
            ConversaRepositoryFake(),
            LeadEventoRepositoryFake(),
        )
        self.agendador = AgendadorFake()
        receber = ReceberMensagem(leads, conversas, eventos, self.agendador)
        self.turno = ProcessarTurno(
            leads,
            conversas,
            AgenteRoteirizado(*respostas),
            CatalogoFake(item("A-1")),
            eventos=eventos,
            persona=PERSONA,
            trava=TravaFake(),
            agendador=self.agendador,
            canais={Canal.WEB: CanalFake()},
        )
        obter, listar = ObterHistorico(leads, conversas), ListarLeads(leads)
        app = criar_app(
            criar_dependencias(
                receber_mensagem=lambda: receber,
                obter_historico=lambda: obter,
                listar_leads=lambda: listar,
            )
        )
        self.http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")

    async def processar_turnos_agendados(self) -> None:
        for lead_id in dict.fromkeys(self.agendador.agendados):
            await self.turno.executar(lead_id)
        self.agendador.agendados.clear()


async def test_post_retorna_202_sem_a_resposta_e_o_polling_mostra_processando() -> None:
    api = Api(RespostaAgente("Zona sul até 800 mil, anotado!"))

    respostas = [
        await api.http.post("/conversas/mensagens", json={"lead_id": "ana", "texto": t})
        for t in ("procuro apê", "zona sul", "até 800 mil")
    ]
    durante = (await api.http.get("/conversas/ana/mensagens")).json()

    assert [r.status_code for r in respostas] == [202, 202, 202]
    assert respostas[0].json()["status"] == "pendente"
    assert "resposta" not in respostas[0].json()
    assert durante["processando"] is True
    assert [m["status"] for m in durante["mensagens"]] == ["pendente"] * 3

    await api.processar_turnos_agendados()
    depois = (await api.http.get("/conversas/ana/mensagens")).json()

    assert depois["processando"] is False
    assert [(m["papel"], m["status"]) for m in depois["mensagens"]] == [
        ("lead", "processada"),
        ("lead", "processada"),
        ("lead", "processada"),
        ("assistente", "enviada"),
    ]
    assert depois["mensagens"][-1]["texto"] == "Zona sul até 800 mil, anotado!"


async def test_historico_de_lead_desconhecido_e_vazio() -> None:
    resposta = await Api().http.get("/conversas/ninguem/mensagens")
    assert resposta.json() == {
        "lead_id": "ninguem",
        "conversa_id": None,
        "processando": False,
        "mensagens": [],
    }


async def test_listar_leads() -> None:
    api = Api()
    await api.http.post("/conversas/mensagens", json={"lead_id": "ana", "texto": "oi"})
    assert [lead["lead_id"] for lead in (await api.http.get("/leads")).json()] == ["ana"]


@pytest.mark.parametrize(
    "corpo",
    [
        {"lead_id": "ana", "texto": ""},
        {"lead_id": "com espaço", "texto": "oi"},
        {"lead_id": "", "texto": "oi"},
        {"texto": "oi"},
    ],
)
async def test_entrada_invalida_retorna_422(corpo: dict[str, str]) -> None:
    resposta = await Api().http.post("/conversas/mensagens", json=corpo)
    assert resposta.status_code == 422
