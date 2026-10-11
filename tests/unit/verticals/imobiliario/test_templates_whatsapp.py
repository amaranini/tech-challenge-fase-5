"""Templates lógicos da imobiliária: preenchimento a partir da ficha/agendamento e o
documento de submissão à Meta em dia."""

from dataclasses import replace
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from sdr.bootstrap import texto_templates_whatsapp
from sdr.core.domain.agenda import Agendamento, Responsavel, StatusAgendamento
from sdr.core.domain.catalogo import ItemCatalogo
from sdr.core.domain.conversa import Canal, Lead
from sdr.core.domain.qualificacao import Qualificacao
from sdr.core.domain.template import ContextoTemplate, preencher_template
from sdr.verticals.imobiliario.followup.templates import (
    CONVITE_VISITA,
    LEMBRETE_AGENDAMENTO,
    NOVIDADE_IMOVEL,
    RESPOSTA_EQUIPE,
    TEMPLATES,
)
from tests.apoio.fakes import FUSO_SP

RAIZ = Path(__file__).resolve().parents[4]


def contexto(
    intencao: str | None = None, ficha: dict[str, object] | None = None, **extra: object
) -> ContextoTemplate:
    lead = Lead.novo(Canal.WHATSAPP, "+5511987654321", "Ana Souza")
    if intencao:
        lead = replace(
            lead, qualificacao=Qualificacao(lead.id, intencao, fichas={intencao: ficha or {}})
        )
    return ContextoTemplate(lead, FUSO_SP, **extra)  # type: ignore[arg-type]


def test_novidade_traz_a_busca_da_ficha_e_o_imovel_do_catalogo() -> None:
    ctx = contexto(
        "compra",
        {"tipo_imovel": "apartamento", "quartos": 2, "regiao": "Pinheiros"},
        itens_novos=(ItemCatalogo("IMV-012", "Apê 2 quartos na Vila Mariana", "x"),),
    )
    p = preencher_template(NOVIDADE_IMOVEL, ctx)
    assert p.fallbacks == ()
    assert p.texto == (
        "Oi, Ana! Apareceu uma opção que combina com a sua busca (apartamento de 2 quartos "
        "em Pinheiros): IMV-012, Apê 2 quartos na Vila Mariana. Quer que eu te conte mais?"
    )


def test_sem_dado_na_ficha_usa_o_padrao_sem_inventar() -> None:
    p = preencher_template(NOVIDADE_IMOVEL, contexto("aluguel", {}))
    assert p.fallbacks == ("busca", "imovel_novo")
    assert "o seu novo imóvel" in p.texto
    convite = preencher_template(CONVITE_VISITA, contexto("investimento", {"regiao": "Moema"}))
    assert "investimento em imóveis em Moema" in convite.texto
    assert "especialista em investimentos" in convite.texto


def test_lembrete_com_dia_hora_no_fuso_e_responsavel() -> None:
    inicio = datetime(2026, 10, 15, 14, 30, tzinfo=FUSO_SP)
    agendamento = Agendamento(
        uuid4(), uuid4(), Responsavel(uuid4(), "Rafael Souza", "corretor"), uuid4(), inicio,
        inicio, "visita_imovel", "presencial", StatusAgendamento.ATIVO, inicio,
    )  # fmt: skip
    p = preencher_template(LEMBRETE_AGENDAMENTO, contexto(agendamento=agendamento))
    assert p.texto == (
        "Oi, Ana! Passando para lembrar da sua visita aos imóveis com Rafael Souza, marcada "
        "para quinta, 15/10, às 14h30 (no imóvel). Continua de pé? Se precisar, eu remarco "
        "para você."
    )


def test_resposta_da_equipe_traz_quem_atende() -> None:
    p = preencher_template(RESPOSTA_EQUIPE, contexto(responsavel="Rafael"))
    assert p.texto.startswith("Oi, Ana! Aqui é Rafael, da imobiliária.")


def test_todos_os_templates_tem_nome_unico_e_padroes_validos() -> None:
    assert len({t.nome for t in TEMPLATES}) == len(TEMPLATES)
    for template in TEMPLATES:  # sem nada no contexto, tudo cai no padrão — e é válido
        preenchido = preencher_template(template, contexto())
        assert "{{" not in preenchido.texto


def test_documento_para_a_meta_esta_em_dia() -> None:
    """Se falhar: `uv run python -m sdr.cli templates-doc` e commite o doc."""
    atual = (RAIZ / "docs" / "whatsapp-templates.md").read_text(encoding="utf-8")
    assert atual == texto_templates_whatsapp()
