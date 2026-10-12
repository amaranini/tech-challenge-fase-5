"""Revisão de vazamento de PII (ADR 015): logs (inclusive traceback) e exportações."""

import json
import logging
from pathlib import Path
from typing import Any, cast

import pytest

from sdr.core.adapters.logs import FiltroPII
from sdr.core.adapters.outbound.persistence.crm_postgres import CRMPostgresMock
from sdr.core.domain.agente import RespostaAgente
from tests.unit.core.test_processar_turno import Cenario

TELEFONE, EMAIL, CPF = "+5511987654321", "ana.souza@exemplo.com", "123.456.789-09"
PII = (TELEFONE, EMAIL, CPF, "11987654321")


def _linhas(caplog: pytest.LogCaptureFixture) -> str:
    formatador = logging.Formatter("%(message)s")
    return "\n".join(formatador.format(r) for r in caplog.records)


@pytest.fixture
def caplog_mascarado(caplog: pytest.LogCaptureFixture) -> pytest.LogCaptureFixture:
    caplog.handler.addFilter(FiltroPII())
    caplog.set_level(logging.DEBUG)
    return caplog


def test_traceback_de_excecao_sai_mascarado(caplog_mascarado: pytest.LogCaptureFixture) -> None:
    try:
        raise ValueError(f"remetente inválido: {TELEFONE} / {EMAIL}")
    except ValueError:
        logging.getLogger("teste").exception("falhou para %s", CPF)

    texto = _linhas(caplog_mascarado)
    assert "ValueError" in texto
    for dado in PII:
        assert dado not in texto, dado


async def test_turno_com_pii_na_fala_nao_vaza_nos_logs(
    caplog_mascarado: pytest.LogCaptureFixture,
) -> None:
    cenario = Cenario(RespostaAgente("Tenho o A-99!"), RespostaAgente(f"Anotei {EMAIL}"))
    cenario.chega(f"meu tel {TELEFONE}, email {EMAIL}, cpf {CPF}")

    await cenario.processar()

    texto = _linhas(caplog_mascarado)
    assert caplog_mascarado.records  # houve log (código inventado ⇒ aviso)
    for dado in PII:
        assert dado not in texto, dado


async def test_log_de_exportacao_do_crm_sai_mascarado(tmp_path: Path) -> None:
    arquivo = tmp_path / "crm.jsonl"
    crm = CRMPostgresMock(cast(Any, None), arquivo)
    await crm._registrar_log(
        {"operacao": "criado", "crm_id": "CRM-1", "lead": {"remetente_id": TELEFONE, "e": EMAIL}}
    )
    linha = arquivo.read_text(encoding="utf-8")
    assert json.loads(linha)["crm_id"] == "CRM-1"
    for dado in PII:
        assert dado not in linha, dado
