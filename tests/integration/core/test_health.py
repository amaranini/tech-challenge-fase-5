import httpx
import pytest

from sdr.bootstrap import criar_aplicacao

pytestmark = pytest.mark.integration


async def test_health_com_postgres_real() -> None:
    app = criar_aplicacao()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://teste"
    ) as cliente:
        resposta = await cliente.get("/health")

    assert resposta.status_code == 200, resposta.text
    assert resposta.json() == {
        "status": "ok",
        "componentes": [{"componente": "postgres", "ok": True, "detalhe": None}],
    }
