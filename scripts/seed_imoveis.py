"""Carrega data/imoveis.json no banco e gera os embeddings (idempotente: upsert + reindexa).

Uso:
    uv run python scripts/seed_imoveis.py                 # do host (banco em localhost:5433)
    docker compose exec api python scripts/seed_imoveis.py
"""

import argparse
import asyncio
import logging
import time
from pathlib import Path

from sdr.adapters.inbound.cli.carga_imoveis import ler_imoveis_json
from sdr.bootstrap import montar_container

CAMINHO_PADRAO = Path(__file__).resolve().parent.parent / "data" / "imoveis.json"


async def main(caminho: Path) -> None:
    imoveis = ler_imoveis_json(caminho)
    container = montar_container()
    try:
        inicio = time.perf_counter()
        total = await container.cadastrar_imoveis.executar(imoveis)
        logging.info(
            "%d imóveis carregados e indexados em %.1fs", total, time.perf_counter() - inicio
        )
    finally:
        await container.encerrar()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arquivo", type=Path, default=CAMINHO_PADRAO)
    asyncio.run(main(parser.parse_args().arquivo))
