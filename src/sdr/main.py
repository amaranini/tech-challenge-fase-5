"""Entrypoint ASGI: `uvicorn sdr.main:app`."""

from sdr.bootstrap import criar_aplicacao

app = criar_aplicacao()
