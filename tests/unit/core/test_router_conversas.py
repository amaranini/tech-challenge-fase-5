import httpx
import pytest

from sdr.core.adapters.inbound.http.app import criar_app
from sdr.core.application.ports.llm import LLMIndisponivelError
from sdr.core.application.use_cases.consultar_conversas import ListarLeads, ObterHistorico
from sdr.core.application.use_cases.processar_mensagem_recebida import ProcessarMensagemRecebida
from sdr.core.domain.agente import Persona, RespostaAgente
from tests.apoio.fakes import (
    AgenteRoteirizado,
    CatalogoFake,
    ConversaRepositoryFake,
    LeadEventoRepositoryFake,
    LeadRepositoryFake,
    item,
)
from tests.apoio.http import criar_dependencias

PERSONA = Persona("Ana", "v1", "...", padrao_codigo_item=r"\bA-\d+\b")


def cliente(*respostas: RespostaAgente | Exception) -> httpx.AsyncClient:
    leads, conversas = LeadRepositoryFake(), ConversaRepositoryFake()
    processar = ProcessarMensagemRecebida(
        leads,
        conversas,
        AgenteRoteirizado(*respostas),
        CatalogoFake(item("A-1")),
        eventos=LeadEventoRepositoryFake(),
        persona=PERSONA,
    )
    obter, listar = ObterHistorico(leads, conversas), ListarLeads(leads)
    app = criar_app(
        criar_dependencias(
            processar_mensagem=lambda: processar,
            obter_historico=lambda: obter,
            listar_leads=lambda: listar,
        )
    )
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://teste")


async def test_conversa_pela_api_e_retoma_pelo_lead_id() -> None:
    api = cliente(
        RespostaAgente("Oi! Comprar ou alugar?"),
        RespostaAgente("Veja o A-1!", itens_consultados=(item("A-1"),)),
    )

    primeira = await api.post("/conversas/mensagens", json={"lead_id": "ana", "texto": "oi"})
    segunda = await api.post("/conversas/mensagens", json={"lead_id": "ana", "texto": "comprar"})
    historico = await api.get("/conversas/mensagens", params={"lead_id": "ana"})
    leads = await api.get("/leads")

    assert primeira.status_code == segunda.status_code == 200
    assert primeira.json()["conversa_id"] == segunda.json()["conversa_id"]
    assert segunda.json()["itens_sugeridos"][0]["id"] == "A-1"
    textos = [m["texto"] for m in historico.json()["mensagens"]]
    assert textos == ["oi", "Oi! Comprar ou alugar?", "comprar", "Veja o A-1!"]
    assert historico.json()["mensagens"][3]["itens_citados"][0]["id"] == "A-1"
    assert [lead["lead_id"] for lead in leads.json()] == ["ana"]


async def test_historico_de_lead_desconhecido_e_vazio() -> None:
    resposta = await cliente().get("/conversas/mensagens", params={"lead_id": "ninguem"})
    assert resposta.json() == {"lead_id": "ninguem", "conversa_id": None, "mensagens": []}


async def test_llm_indisponivel_retorna_503() -> None:
    api = cliente(LLMIndisponivelError("sem chave"))
    resposta = await api.post("/conversas/mensagens", json={"lead_id": "ana", "texto": "oi"})
    assert resposta.status_code == 503


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
    resposta = await cliente().post("/conversas/mensagens", json=corpo)
    assert resposta.status_code == 422
