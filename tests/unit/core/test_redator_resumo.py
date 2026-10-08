"""RedatorResumoLLM: schema só das seções redigidas, ids de itens restritos aos citados."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sdr.core.adapters.outbound.agent.redator_resumo import RedatorResumoLLM, schema_resumo
from sdr.core.domain.agenda import Agendamento, Responsavel, StatusAgendamento
from sdr.core.domain.conversa import Mensagem, Papel
from sdr.core.domain.resumo import FatosResumo, ItemCitado
from tests.apoio.fakes import FUSO_SP, LLMRoteirizado
from tests.unit.core.test_resumo_handoff import TEMPLATE


def fatos() -> FatosResumo:
    cid = uuid4()
    return FatosResumo(
        lead_id=uuid4(),
        intencao="plano",
        ficha={"unidade": "Centro"},
        campos_faltantes=("orcamento",),
        score=None,
        proxima_acao=None,
        agendamento=None,
        mensagens=(
            Mensagem.nova(cid, Papel.LEAD, "quero treinar"),
            Mensagem.nova(cid, Papel.AGENTE, "tenho o P-1"),
        ),
        itens={"P-1": ItemCitado("P-1", "Plano anual")},
    )


def test_schema_tem_so_as_secoes_redigidas() -> None:
    schema = schema_resumo(TEMPLATE, fatos())
    assert schema["required"] == ["perfil", "itens", "objecoes", "perguntas", "trechos"]
    itens = schema["properties"]["itens"]["items"]["properties"]["id"]  # type: ignore[index]
    assert itens == {"type": "string", "enum": ["P-1"]}


async def test_redige_com_dados_e_conversa_completa() -> None:
    llm = LLMRoteirizado(estruturadas=[{"perfil": "Quer treinar."}])

    rascunho = await RedatorResumoLLM(llm, FUSO_SP).redigir(fatos(), TEMPLATE)

    assert rascunho.secoes == {"perfil": "Quer treinar."}
    mensagens, _, nome = llm.chamadas_estruturadas[0]
    assert nome == "resumo_handoff"
    assert '"campos_nao_informados": [\n  "orcamento"' in mensagens[0].conteudo
    assert "palavra por palavra" in mensagens[0].conteudo
    assert 'nunca\n  "hoje" ou "amanhã"' in mensagens[0].conteudo
    assert mensagens[1].conteudo.endswith("Lead: quero treinar\nAssistente: tenho o P-1")


async def test_agendamento_vai_com_data_absoluta_no_fuso_da_operacao() -> None:
    inicio = datetime(2026, 10, 8, 16, 0, tzinfo=UTC)  # 13h em São Paulo
    ag = Agendamento(
        uuid4(), uuid4(), Responsavel(uuid4(), "Sol", "consultora"), uuid4(), inicio,
        inicio + timedelta(hours=1), "aula", "presencial", StatusAgendamento.ATIVO, inicio,
    )  # fmt: skip
    llm = LLMRoteirizado(estruturadas=[{}])

    await RedatorResumoLLM(llm, FUSO_SP).redigir(replace(fatos(), agendamento=ag), TEMPLATE)

    assert '"quando": "quinta, 08/10/2026 às 13:00"' in llm.chamadas_estruturadas[0][0][0].conteudo


async def test_checar_le_frases_por_secao_e_ignora_invalidas() -> None:
    llm = LLMRoteirizado(
        estruturadas=[
            {
                "perfil": [
                    {"frase": "Quer treinar.", "fonte": "conversa", "sustentada": True,
                     "evidencias": ["Lead: quero treinar\nLead: à noite"]},
                    {"frase": "É rico.", "fonte": "chute", "sustentada": True, "evidencias": []},
                    {"frase": "Levar o P-1.", "fonte": "recomendacao", "sustentada": "sim"},
                ]
            }
        ]
    )  # fmt: skip

    checagem = await RedatorResumoLLM(llm, FUSO_SP).checar(fatos(), {"perfil": "..."})

    assert checagem["perfil"][0].evidencias == ("quero treinar", "à noite")
    assert [(a.frase, a.sustentada) for a in checagem["perfil"]] == [
        ("Quer treinar.", True),
        ("Levar o P-1.", False),  # só True explícito conta
    ]
    mensagens, schema, nome = llm.chamadas_estruturadas[0]
    assert nome == "checagem_resumo"
    assert schema["required"] == ["perfil"]
    assert "[perfil] ..." in mensagens[1].conteudo
