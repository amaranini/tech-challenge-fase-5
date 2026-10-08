"""Leitura direta do banco do compose (o que não tem rota): o CRM mock."""

from typing import Any

from sqlalchemy import create_engine, text

from sdr.config.settings import obter_settings


def registro_crm(remetente_id: str) -> dict[str, Any] | None:
    engine = create_engine(obter_settings().database_url, connect_args={"connect_timeout": 2})
    try:
        with engine.connect() as conn:
            linha = conn.execute(
                text(
                    "SELECT c.crm_id, c.resumo_versao, c.resumo, c.dados FROM crm_registros c "
                    "JOIN leads l ON l.id = c.lead_id WHERE l.remetente_id = :r"
                ),
                {"r": remetente_id},
            ).first()
    finally:
        engine.dispose()
    return dict(linha._mapping) if linha else None
