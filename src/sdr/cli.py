"""CLI administrativa.

Uso:
    uv run python -m sdr.cli seed                     # do host (banco em localhost:5433)
    docker compose exec api python -m sdr.cli seed
    uv run python -m sdr.cli templates-doc            # gera docs/whatsapp-templates.md
    uv run python -m sdr.cli hash-senha               # DASHBOARD_SENHA_HASH do login
"""

import argparse
import asyncio
import getpass
import hashlib
import logging
import secrets
import time
from pathlib import Path

from sdr.bootstrap import montar_container, texto_templates_whatsapp
from sdr.core.adapters.logs import instalar_mascara_pii

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


async def _hash_senha() -> None:
    """Hash PBKDF2 para DASHBOARD_SENHA_HASH (a senha não fica no env em texto)."""
    senha = getpass.getpass("Senha do dashboard: ")
    if not senha or senha != getpass.getpass("Repita: "):
        raise SystemExit("senhas vazias ou diferentes")
    sal = secrets.token_bytes(16)
    calculado = hashlib.pbkdf2_hmac("sha256", senha.encode(), sal, ITERACOES_SENHA).hex()
    print(f"DASHBOARD_SENHA_HASH=pbkdf2_sha256:{ITERACOES_SENHA}:{sal.hex()}:{calculado}")


ITERACOES_SENHA = 600_000  # recomendação OWASP para PBKDF2-SHA256

COMANDOS = {"seed": _seed, "templates-doc": _templates_doc, "hash-senha": _hash_senha}


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    instalar_mascara_pii()
    parser = argparse.ArgumentParser(prog="python -m sdr.cli")
    parser.add_argument("comando", choices=sorted(COMANDOS))
    asyncio.run(COMANDOS[parser.parse_args().comando]())


if __name__ == "__main__":
    main()
