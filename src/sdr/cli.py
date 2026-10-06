"""CLI administrativa.

Uso:
    uv run python -m sdr.cli seed                     # do host (banco em localhost:5433)
    docker compose exec api python -m sdr.cli seed
"""

import argparse
import asyncio
import logging
import time

from sdr.bootstrap import montar_container


async def _seed() -> None:
    """Carrega o catálogo inicial da vertical ativa (idempotente: upsert + reindexa)."""
    container = montar_container()
    try:
        inicio = time.perf_counter()
        total = await container.vertical.carregar_catalogo_inicial()
        logging.info(
            "%d itens carregados e indexados em %.1fs", total, time.perf_counter() - inicio
        )
    finally:
        await container.encerrar()


COMANDOS = {"seed": _seed}


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(prog="python -m sdr.cli")
    parser.add_argument("comando", choices=sorted(COMANDOS))
    asyncio.run(COMANDOS[parser.parse_args().comando]())


if __name__ == "__main__":
    main()
