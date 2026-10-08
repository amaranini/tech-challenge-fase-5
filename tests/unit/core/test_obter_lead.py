"""ObterLead (caso de uso) e GET /leads/{lead_id} — com fakes e a vertical fictícia."""

from dataclasses import replace
from datetime import timedelta

import httpx

from sdr.core.adapters.inbound.http.app import criar_app
from sdr.core.application.use_cases.obter_lead import ObterLead
from sdr.core.domain.conversa import Canal, Lead
from sdr.core.domain.eventos import EventoLead, TipoEvento
from sdr.core.domain.qualificacao import Classificacao, Score
from tests.apoio.fakes import LeadEventoRepositoryFake, LeadRepositoryFake
from tests.apoio.http import criar_dependencias
from tests.apoio.vertical_fake import INTENCOES


class Cenario:
    def __init__(self) -> None:
        self.leads = LeadRepositoryFake()
        self.eventos = LeadEventoRepositoryFake()
        self.obter = ObterLead(self.leads, self.eventos, INTENCOES)

    async def lead(self, remetente: str, **qualificacao: object) -> Lead:
        lead = Lead.novo(Canal.WEB, remetente)
        lead = replace(lead, qualificacao=replace(lead.qualificacao, **qualificacao))
        await self.leads.salvar(lead)
        return lead

    def http(self) -> httpx.AsyncClient:
        app = criar_app(criar_dependencias(obter_lead=lambda: self.obter))
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


async def test_campos_faltantes_seguem_a_prioridade_da_intencao_atual() -> None:
    cenario = Cenario()
    await cenario.lead(
        "ana",
        intencao_atual="plano",
        fichas={"plano": {"unidade": "Centro"}, "avulso": {"data": "amanhã"}},
    )

    estado = await cenario.obter.executar(Canal.WEB, "ana")

    assert estado is not None
    # prioridade do PLANO: orcamento, unidade, horario (a ficha do "avulso" não interfere)
    assert estado.campos_faltantes == ["orcamento", "horario"]


async def test_sem_intencao_ou_intencao_desconhecida_nao_tem_faltantes() -> None:
    cenario = Cenario()
    await cenario.lead("novo")
    await cenario.lead("antigo", intencao_atual="extinta", fichas={"extinta": {"x": 1}})

    novo = await cenario.obter.executar(Canal.WEB, "novo")
    antigo = await cenario.obter.executar(Canal.WEB, "antigo")

    assert novo is not None
    assert novo.campos_faltantes == []
    assert antigo is not None
    assert antigo.campos_faltantes == []


async def test_lead_inexistente_ou_de_outro_canal() -> None:
    cenario = Cenario()
    await cenario.leads.salvar(Lead.novo(Canal.WHATSAPP, "ana"))

    assert await cenario.obter.executar(Canal.WEB, "ana") is None
    assert await cenario.obter.executar(Canal.WEB, "ninguem") is None


async def test_get_lead_expoe_qualificacao_completa_e_eventos_em_ordem() -> None:
    cenario = Cenario()
    lead = Lead.novo(Canal.WEB, "ana")
    t0 = lead.criado_em
    lead = await cenario.lead(
        "ana",
        intencao_atual="plano",
        fichas={
            "avulso": {"unidade": "Centro", "data": "sábado"},
            "plano": {"unidade": "Centro", "orcamento": 150, "horario": "noite"},
        },
        score=Score(75, Classificacao.QUENTE, ("+50 orçamento", "+25 unidade")),
        proxima_acao="agendar_aula",
        qualificado_em=t0,
    )
    await cenario.eventos.registrar(
        [
            EventoLead(lead.id, TipoEvento.LEAD_CRIADO, t0, {"canal": "web"}),
            EventoLead(
                lead.id,
                TipoEvento.INTENCAO_ALTERADA,
                t0 + timedelta(seconds=1),
                {"de": "avulso", "para": "plano", "confianca": 0.9},
            ),
            EventoLead(
                lead.id,
                TipoEvento.LEAD_QUALIFICADO,
                t0 + timedelta(seconds=2),
                {"intencao": "plano", "proxima_acao": "agendar_aula"},
            ),
        ]
    )

    resposta = await cenario.http().get("/leads/ana")

    assert resposta.status_code == 200
    corpo = resposta.json()
    assert {k: v for k, v in corpo.items() if k not in ("criado_em", "eventos")} == {
        "lead_id": "ana",
        "canal": "web",
        "nome": None,
        "intencao": "plano",
        "ficha": {"unidade": "Centro", "orcamento": 150, "horario": "noite"},
        "fichas": {
            "avulso": {"unidade": "Centro", "data": "sábado"},
            "plano": {"unidade": "Centro", "orcamento": 150, "horario": "noite"},
        },
        "campos_faltantes": [],
        "score": 75,
        "classificacao": "quente",
        "score_motivos": ["+50 orçamento", "+25 unidade"],
        "proxima_acao": "agendar_aula",
        "qualificado_em": corpo["qualificado_em"],
        "agendamento": None,
        "atendimento": {
            "estado": "atendimento_ia",
            "desde": None,
            "na_fila_desde": None,
            "motivo": None,
            "responsavel": None,
        },
    }
    assert corpo["qualificado_em"] is not None
    assert [(e["tipo"], e["payload"]) for e in corpo["eventos"]] == [
        ("LeadCriado", {"canal": "web"}),
        ("IntencaoAlterada", {"de": "avulso", "para": "plano", "confianca": 0.9}),
        ("LeadQualificado", {"intencao": "plano", "proxima_acao": "agendar_aula"}),
    ]


async def test_get_lead_recem_criado_sem_qualificacao() -> None:
    cenario = Cenario()
    await cenario.lead("bia")

    corpo = (await cenario.http().get("/leads/bia")).json()

    assert corpo["intencao"] is None
    assert corpo["ficha"] == {}
    assert corpo["fichas"] == {}
    assert corpo["campos_faltantes"] == []
    assert (corpo["score"], corpo["classificacao"], corpo["score_motivos"]) == (None, None, [])
    assert corpo["proxima_acao"] is None
    assert corpo["eventos"] == []


async def test_get_lead_inexistente_retorna_404() -> None:
    resposta = await Cenario().http().get("/leads/ninguem")
    assert resposta.status_code == 404
