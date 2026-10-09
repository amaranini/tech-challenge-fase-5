"""CLI administrativa.

Uso:
    uv run python -m sdr.cli seed                     # do host (banco em localhost:5433)
    docker compose exec api python -m sdr.cli seed
    uv run python -m sdr.cli templates-doc            # gera docs/whatsapp-templates.md
"""

import argparse
import asyncio
import logging
import time
from pathlib import Path

from sdr.bootstrap import montar_container, texto_templates_whatsapp

DOC_TEMPLATES = Path("docs/whatsapp-templates.md")


async def _seed() -> None:
    """Carrega o catálogo inicial da vertical ativa (idempotente: upsert + reindexa) e a
    agenda mock (responsáveis + grade dos próximos dias úteis)."""
    container = montar_container()
    try:
        inicio = time.perf_counter()
        total = await container.vertical.carregar_catalogo_inicial()
        logging.info(
            "%d itens carregados e indexados em %.1fs", total, time.perf_counter() - inicio
        )
        slots = await container.semear_agenda()
        logging.info("Agenda mock: responsáveis garantidos, %d slot(s) novo(s)", slots)
    finally:
        await container.encerrar()


async def _templates_doc() -> None:
    """Textos de referência dos templates lógicos da vertical ativa, para a Meta."""
    texto = texto_templates_whatsapp()
    await asyncio.to_thread(DOC_TEMPLATES.write_text, texto, encoding="utf-8")
    logging.info("%s atualizado", DOC_TEMPLATES)


COMANDOS = {"seed": _seed, "templates-doc": _templates_doc}


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(prog="python -m sdr.cli")
    parser.add_argument("comando", choices=sorted(COMANDOS))
    asyncio.run(COMANDOS[parser.parse_args().comando]())


if __name__ == "__main__":
    main()
