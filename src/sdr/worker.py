"""Worker de follow-up: processo separado que só chama o caso de uso em laço.

Nenhuma regra aqui — cadência, elegibilidade, SLA e envio estão no core
(`ExecutarFollowUps`). Seguro para várias réplicas: a fila é consumida com SKIP LOCKED.

Uso:
    docker compose up -d worker
    uv run python -m sdr.worker            # do host (banco em localhost:5433)
"""

import asyncio
import contextlib
import logging
import signal

from sdr.bootstrap import montar_container
from sdr.config.settings import obter_settings
from sdr.core.adapters.logs import instalar_mascara_pii

logger = logging.getLogger("sdr.worker")


async def rodar(parar: asyncio.Event) -> None:
    settings = obter_settings()
    container = montar_container(settings)
    if container.executar_followups is None:
        logger.warning("A vertical ativa não define cadência de follow-up: nada a fazer")
        return
    logger.info(
        "Worker de follow-up no ar (unidade=%s, intervalo=%ss)",
        settings.followup_unidade,
        settings.followup_intervalo_segundos,
    )
    try:
        while not parar.is_set():
            tratados = await container.executar_followups.executar(settings.followup_lote)
            if tratados:
                logger.info("%d follow-up(s) tratado(s)", tratados)
                continue
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(parar.wait(), settings.followup_intervalo_segundos)
    finally:
        await container.encerrar()


def main() -> None:
    logging.basicConfig(level=obter_settings().log_level, format="%(asctime)s %(name)s %(message)s")
    instalar_mascara_pii()
    parar = asyncio.Event()

    async def principal() -> None:
        laco = asyncio.get_running_loop()
        for sinal in (signal.SIGINT, signal.SIGTERM):
            laco.add_signal_handler(sinal, parar.set)
        await rodar(parar)

    asyncio.run(principal())


if __name__ == "__main__":
    main()
