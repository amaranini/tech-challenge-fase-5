"""Logs sem dados pessoais: filtro que mascara telefone, e-mail e CPF em toda mensagem
(inclusive no access log do uvicorn, que traz o lead_id "whatsapp:+55..." no caminho)."""

import logging
from collections.abc import Iterable

from sdr.core.domain.pii import mascarar_pii

LOGGERS_PADRAO = ("", "uvicorn", "uvicorn.access", "uvicorn.error")


class FiltroPII(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = mascarar_pii(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(_mascarar(a) for a in record.args)
        elif isinstance(record.args, dict):
            record.args = {k: _mascarar(v) for k, v in record.args.items()}
        return True


def _mascarar(valor: object) -> object:
    return mascarar_pii(valor) if isinstance(valor, str) else valor


def instalar_mascara_pii(nomes: Iterable[str] = LOGGERS_PADRAO) -> None:
    """Põe o filtro nos handlers (os registros propagados passam por eles)."""
    filtro = FiltroPII()
    for nome in nomes:
        for handler in logging.getLogger(nome or None).handlers:
            if not any(isinstance(f, FiltroPII) for f in handler.filters):
                handler.addFilter(filtro)
