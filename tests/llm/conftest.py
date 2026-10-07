"""Os cenários reservam horários de verdade na agenda mock da API no ar (banco do compose).

Ao fim de cada cenário, devolve à agenda os slots que os leads de teste (`llm-*`)
ocuparam — sem isso, rodadas seguidas esgotam a "quinta à tarde" do corretor e o roteiro
passa a depender do que sobrou. Os agendamentos ficam como cancelados (trilha preservada).
"""

from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

from sdr.config.settings import obter_settings

LIBERAR_AGENDA_DE_TESTE = text(
    """
    WITH de_teste AS (
        UPDATE agendamentos a SET status = 'cancelado'
        FROM leads l
        WHERE l.id = a.lead_id AND l.remetente_id LIKE 'llm-%' AND a.status = 'ativo'
        RETURNING a.id
    )
    UPDATE slots_agenda SET agendamento_id = NULL
    WHERE agendamento_id IN (SELECT id FROM de_teste)
    """
)


@pytest.fixture(autouse=True)
def liberar_agenda_de_teste() -> Iterator[None]:
    yield
    engine = create_engine(obter_settings().database_url, connect_args={"connect_timeout": 2})
    try:
        with engine.begin() as conn:
            conn.execute(LIBERAR_AGENDA_DE_TESTE)
    except OperationalError:
        pass  # sem banco acessível do host: os cenários já terão sido pulados
    finally:
        engine.dispose()
