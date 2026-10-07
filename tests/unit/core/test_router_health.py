import httpx
import pytest

from sdr.core.adapters.inbound.http.app import criar_app
from sdr.core.application.ports.saude import ResultadoVerificacao
from sdr.core.application.use_cases.verificar_saude import VerificarSaude
from tests.apoio.http import criar_dependencias


class VerificadorFake:
    def __init__(self, ok: bool) -> None:
        self._ok = ok

    async def verificar(self) -> ResultadoVerificacao:
        return ResultadoVerificacao("postgres", ok=self._ok, detalhe=None if self._ok else "fora")


@pytest.mark.parametrize(
    ("ok", "status_http", "status_corpo"),
    [(True, 200, "ok"), (False, 503, "degradado")],
)
async def test_health(ok: bool, status_http: int, status_corpo: str) -> None:
    app = criar_app(
        criar_dependencias(verificar_saude=lambda: VerificarSaude([VerificadorFake(ok)]))
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://teste"
    ) as cliente:
        resposta = await cliente.get("/health")

    assert resposta.status_code == status_http
    assert resposta.json()["status"] == status_corpo
