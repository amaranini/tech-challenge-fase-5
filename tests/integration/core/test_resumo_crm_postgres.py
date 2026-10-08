"""Resumos versionados, CRM mock (upsert + log JSON) e listagem de agendamentos no Postgres."""

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sdr.core.adapters.outbound.persistence.crm_postgres import CRMPostgresMock
from sdr.core.adapters.outbound.persistence.modelos import CrmRegistroModel
from sdr.core.adapters.outbound.persistence.repositorios_conversa_sql import LeadRepositorySql
from sdr.core.adapters.outbound.persistence.resumos_sql import ResumoRepositorySql
from sdr.core.domain.agenda import StatusAgendamento
from sdr.core.domain.conversa import Canal, Lead
from sdr.core.domain.resumo import NAO_INFORMADO, Resumo, SecaoPreenchida, TipoSecao
from tests.integration.core.test_agenda_postgres import (
    FIM_JANELA,
    INICIO_JANELA,
    SOL,
    _agenda,
    _pedido,
)

pytestmark = pytest.mark.integration


def resumo(lead: Lead, versao: int) -> Resumo:
    return Resumo(
        id=uuid4(),
        lead_id=lead.id,
        versao=versao,
        gerado_em=datetime.now(UTC),
        gatilho="LeadQualificado",
        template_versao="t1",
        titulo="Resumo",
        secoes=(
            SecaoPreenchida("perfil", "Perfil", TipoSecao.TEXTO, f"perfil v{versao}"),
            SecaoPreenchida("objecoes", "Objeções", TipoSecao.LISTA, NAO_INFORMADO),
            SecaoPreenchida("ficha", "Ficha", TipoSecao.FICHA, {"regiao": "zona sul"}),
        ),
        impressao="x" * 64,
        descartados=("objecoes: sem lastro",),
        modelo="fake",
    )


async def _lead(sessoes: async_sessionmaker[AsyncSession]) -> Lead:
    lead = Lead.novo(Canal.WEB, f"lead-{uuid4().hex[:6]}")
    await LeadRepositorySql(sessoes).salvar(lead)
    return lead


async def test_versoes_do_resumo(sessoes: async_sessionmaker[AsyncSession]) -> None:
    repo, lead = ResumoRepositorySql(sessoes), await _lead(sessoes)
    v1, v2 = resumo(lead, 1), resumo(lead, 2)
    await repo.salvar(v1)
    await repo.salvar(v2)

    assert await repo.versoes(lead.id) == [1, 2]
    assert await repo.ultimo(lead.id) == v2
    assert await repo.obter(lead.id, 1) == v1
    assert await repo.obter(lead.id, 3) is None
    with pytest.raises(IntegrityError):
        await repo.salvar(resumo(lead, 2))  # versão é única por lead


async def test_crm_mock_cria_e_depois_atualiza_com_log(
    sessoes: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    log = tmp_path / "crm.jsonl"
    crm, lead = CRMPostgresMock(sessoes, log), await _lead(sessoes)

    criado = await crm.registrar(lead, resumo(lead, 1), None)
    atualizado = await crm.registrar(lead, resumo(lead, 2), None)

    assert criado.criado is True
    assert atualizado.criado is False
    assert criado.crm_id == atualizado.crm_id
    async with sessoes() as sessao:
        registro = await sessao.scalar(
            select(CrmRegistroModel).where(CrmRegistroModel.lead_id == lead.id)
        )
    assert registro is not None
    assert registro.resumo_versao == 2
    assert registro.resumo["secoes"][0]["conteudo"] == "perfil v2"
    assert registro.dados["lead"]["remetente_id"] == lead.remetente_id
    linhas = [json.loads(linha) for linha in log.read_text().splitlines()]
    assert [linha["operacao"] for linha in linhas] == ["criar", "atualizar"]
    assert linhas[1]["resumo"]["versao"] == 2


async def test_listar_agendamentos_por_lead_status_e_data(
    sessoes: async_sessionmaker[AsyncSession],
) -> None:
    agenda = await _agenda(sessoes)
    lead_a, lead_b = await _lead(sessoes), await _lead(sessoes)
    slots = await agenda.listar_disponibilidade([SOL.id], INICIO_JANELA, FIM_JANELA)
    a1 = await agenda.reservar(_pedido(lead_a, slots[0].id))
    a2 = await agenda.reservar(_pedido(lead_a, slots[1].id))
    b1 = await agenda.reservar(_pedido(lead_b, slots[2].id))
    await agenda.cancelar(a2.id)

    assert [a.id for a in await agenda.listar_agendamentos()] == [a1.id, a2.id, b1.id]
    assert [a.id for a in await agenda.listar_agendamentos(lead_id=lead_a.id)] == [a1.id, a2.id]
    ativos = await agenda.listar_agendamentos(status=StatusAgendamento.ATIVO)
    assert [a.id for a in ativos] == [a1.id, b1.id]
    depois = await agenda.listar_agendamentos(a_partir_de=a1.fim)
    assert a1.id not in [a.id for a in depois]
