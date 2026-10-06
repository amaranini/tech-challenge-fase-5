from sdr.application.ports.saude import ResultadoVerificacao
from sdr.application.use_cases.verificar_saude import VerificarSaude


class VerificadorFake:
    def __init__(self, componente: str, ok: bool) -> None:
        self._resultado = ResultadoVerificacao(componente, ok=ok)

    async def verificar(self) -> ResultadoVerificacao:
        return self._resultado


async def test_saudavel_quando_todos_componentes_ok() -> None:
    caso_de_uso = VerificarSaude([VerificadorFake("postgres", ok=True)])

    status = await caso_de_uso.executar()

    assert status.ok
    assert [c.componente for c in status.componentes] == ["postgres"]


async def test_degradado_quando_algum_componente_falha() -> None:
    caso_de_uso = VerificarSaude(
        [VerificadorFake("postgres", ok=True), VerificadorFake("llm", ok=False)]
    )

    status = await caso_de_uso.executar()

    assert not status.ok
    assert len(status.componentes) == 2
