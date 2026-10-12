"""Feedback "útil / não útil" do resumo vira evento (página Qualidade da IA)."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from sdr.core.application.use_cases.avaliar_resumo import (
    AvaliacaoResumo,
    AvaliarResumo,
    ResumoInexistenteError,
)
from sdr.core.domain.conversa import Canal, Lead
from sdr.core.domain.eventos import TipoEvento
from sdr.core.domain.resumo import Resumo
from tests.apoio.fakes import (
    LeadEventoRepositoryFake,
    LeadRepositoryFake,
    RelogioFake,
    ResumoRepositoryFake,
)

AGORA = datetime(2026, 10, 12, 15, tzinfo=UTC)


def _resumo(lead: Lead, versao: int) -> Resumo:
    return Resumo(
        id=uuid4(),
        lead_id=lead.id,
        versao=versao,
        gerado_em=AGORA,
        gatilho="LeadQualificado",
        template_versao="t1",
        titulo="Resumo",
        secoes=(),
        impressao="x" * 64,
    )


async def _cenario() -> tuple[AvaliarResumo, LeadEventoRepositoryFake, Lead]:
    leads, resumos, eventos = (
        LeadRepositoryFake(),
        ResumoRepositoryFake(),
        LeadEventoRepositoryFake(),
    )
    lead = Lead.novo(Canal.WEB, "lead-1")
    await leads.salvar(lead)
    await resumos.salvar(_resumo(lead, 1))
    await resumos.salvar(_resumo(lead, 2))
    return AvaliarResumo(leads, resumos, eventos, RelogioFake(AGORA)), eventos, lead


async def test_avaliacao_da_versao_atual_vira_evento() -> None:
    avaliar, eventos, lead = await _cenario()
    await avaliar.executar(
        Canal.WEB,
        "lead-1",
        AvaliacaoResumo(util=False, responsavel="Rafael", comentario=" sem orçamento "),
    )
    [evento] = eventos.eventos
    assert evento.tipo is TipoEvento.RESUMO_AVALIADO
    assert evento.lead_id == lead.id
    assert evento.payload == {
        "util": False,
        "versao": 2,
        "responsavel": "Rafael",
        "comentario": "sem orçamento",
    }


async def test_versao_especifica_e_lead_sem_resumo() -> None:
    avaliar, eventos, _ = await _cenario()
    await avaliar.executar(Canal.WEB, "lead-1", AvaliacaoResumo(util=True, versao=1))
    assert eventos.eventos[0].payload == {"util": True, "versao": 1}
    with pytest.raises(ResumoInexistenteError):
        await avaliar.executar(Canal.WEB, "outro", AvaliacaoResumo(util=True))
    with pytest.raises(ResumoInexistenteError):
        await avaliar.executar(Canal.WEB, "lead-1", AvaliacaoResumo(util=True, versao=9))
