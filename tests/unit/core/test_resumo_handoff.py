"""Resumo de handoff: ancoragem (nada inventado), versões e CRM — sem banco e sem LLM."""

from dataclasses import replace
from datetime import datetime, timedelta
from uuid import uuid4

from sdr.core.application.use_cases.gerar_resumo_handoff import GerarResumoHandoff
from sdr.core.domain.agenda import Agendamento, Responsavel, StatusAgendamento
from sdr.core.domain.atendimento import Atendimento, EstadoAtendimento
from sdr.core.domain.conversa import Canal, Conversa, Lead, Mensagem, Papel
from sdr.core.domain.eventos import EventoLead, TipoEvento
from sdr.core.domain.qualificacao import Classificacao, Qualificacao, Score
from sdr.core.domain.resumo import (
    NAO_INFORMADO,
    AfirmacaoChecada,
    FatosResumo,
    FonteAfirmacao,
    ItemCitado,
    SecaoResumo,
    TemplateResumo,
    TipoSecao,
    ancorar,
    itens_citados,
    podar_textos,
    tem_lastro,
)
from tests.apoio.fakes import (
    FUSO_SP,
    AgendaFake,
    ConversaRepositoryFake,
    CRMFake,
    LeadEventoRepositoryFake,
    LeadRepositoryFake,
    RedatorRoteirizado,
    RelogioFake,
    ResumoRepositoryFake,
)
from tests.apoio.vertical_fake import INTENCOES

AGORA = datetime(2026, 10, 5, 10, 0, tzinfo=FUSO_SP)
TEMPLATE = TemplateResumo(
    versao="t1",
    titulo="Resumo para a consultora",
    secoes=(
        SecaoResumo("perfil", "Perfil", TipoSecao.TEXTO, "quem é"),
        SecaoResumo("ficha", "Necessidades", TipoSecao.FICHA),
        SecaoResumo("score", "Score", TipoSecao.SCORE),
        SecaoResumo("itens", "Planos sugeridos", TipoSecao.ITENS_CATALOGO, "reação"),
        SecaoResumo("objecoes", "Objeções", TipoSecao.LISTA, "receios"),
        SecaoResumo("perguntas", "Perguntas em aberto", TipoSecao.LISTA, "dúvidas"),
        SecaoResumo("agendamento", "Agendamento", TipoSecao.AGENDAMENTO),
        SecaoResumo("trechos", "Trechos-chave", TipoSecao.TRECHOS, "falas"),
    ),
)


def conversa(conversa_id=None):  # type: ignore[no-untyped-def]
    cid = conversa_id or uuid4()
    citados = {"itens_citados": [{"id": "P-1", "titulo": "Plano anual", "resumo": "12x"}]}
    return [
        Mensagem.nova(cid, Papel.LEAD, "Quero treinar na unidade Centro, à noite"),
        Mensagem.nova(cid, Papel.ASSISTENTE, "Tenho o P-1, plano anual.", metadados=citados),
        Mensagem.nova(cid, Papel.LEAD, "Achei o plano anual caro, tem algo mensal?"),
        Mensagem.nova(cid, Papel.LEAD, "Tenho medo de não conseguir ir todo dia"),
    ]


def fatos(**sobrescritas: object) -> FatosResumo:
    mensagens = conversa()
    base = FatosResumo(
        lead_id=uuid4(),
        intencao="plano",
        ficha={"unidade": "Centro", "horario": "noite"},
        campos_faltantes=("orcamento",),
        score=Score(50, Classificacao.MORNO, ("+25 unidade", "+25 horário")),
        proxima_acao=None,
        agendamento=None,
        mensagens=tuple(mensagens),
        itens=itens_citados(mensagens),
    )
    return replace(base, **sobrescritas)  # type: ignore[arg-type]


def conteudo(secoes, chave):  # type: ignore[no-untyped-def]
    return next(s.conteudo for s in secoes if s.chave == chave)


# ---------------------------------------------------------------- ancoragem (domínio)


def test_dados_vem_do_estado_e_o_que_falta_aparece_como_nao_informado() -> None:
    secoes, _ = ancorar({}, TEMPLATE, fatos())

    assert conteudo(secoes, "ficha") == {
        "unidade": "Centro",
        "horario": "noite",
        "orcamento": NAO_INFORMADO,
    }
    assert conteudo(secoes, "score") == {
        "pontos": 50,
        "classificacao": "morno",
        "motivos": ["+25 unidade", "+25 horário"],
    }
    assert conteudo(secoes, "agendamento") == NAO_INFORMADO
    assert conteudo(secoes, "perfil") == NAO_INFORMADO  # LLM não redigiu
    assert conteudo(secoes, "objecoes") == NAO_INFORMADO
    assert conteudo(secoes, "trechos") == NAO_INFORMADO
    # item citado na conversa aparece mesmo sem o LLM mencionar
    assert conteudo(secoes, "itens") == [
        {"id": "P-1", "titulo": "Plano anual", "reacao": NAO_INFORMADO}
    ]


def test_llm_nao_consegue_sobrescrever_dados_do_estado() -> None:
    bruto = {"ficha": {"orcamento": 999}, "score": {"pontos": 100}}
    secoes, _ = ancorar(bruto, TEMPLATE, fatos())
    assert conteudo(secoes, "ficha")["orcamento"] == NAO_INFORMADO  # type: ignore[index]
    assert conteudo(secoes, "score")["pontos"] == 50  # type: ignore[index]


def test_ancoragem_descarta_o_que_nao_tem_lastro() -> None:
    bruto = {
        "perfil": "Quer treinar à noite no Centro.",
        "itens": [
            {"id": "P-1", "reacao": "achou caro", "evidencia": "achei o plano anual caro"},
            {"id": "P-99", "reacao": "amou", "evidencia": "amei"},  # nunca citado
        ],
        "objecoes": [
            {"texto": "Preço do anual", "evidencia": "Achei o plano anual caro"},
            {"texto": "Distância", "evidencia": "é muito longe de casa"},  # inventada
            {"texto": "Frequência", "evidencia": "medo de não conseguir ir todo dia"},
        ],
        "perguntas": [{"texto": "Tem plano mensal?", "evidencia": "tem algo mensal?"}],
        "trechos": [
            "Tenho medo de não conseguir ir todo dia",
            "Quero muito emagrecer",  # ninguém disse
        ],
    }

    secoes, descartados = ancorar(bruto, TEMPLATE, fatos())

    assert conteudo(secoes, "itens") == [
        {"id": "P-1", "titulo": "Plano anual", "reacao": "achou caro"}
    ]
    assert [o["texto"] for o in conteudo(secoes, "objecoes")] == [  # type: ignore[attr-defined]
        "Preço do anual",
        "Frequência",
    ]
    assert conteudo(secoes, "perguntas") == [
        {"texto": "Tem plano mensal?", "evidencia": "tem algo mensal?"}
    ]
    assert conteudo(secoes, "trechos") == ["Tenho medo de não conseguir ir todo dia"]
    assert len(descartados) == 3
    assert any("P-99" in d for d in descartados)
    assert any("Distância" in d for d in descartados)
    assert any("emagrecer" in d for d in descartados)


def test_reacao_sem_evidencia_vira_nao_informado() -> None:
    bruto = {"itens": [{"id": "P-1", "reacao": "adorou", "evidencia": "adorei!!"}]}
    secoes, descartados = ancorar(bruto, TEMPLATE, fatos())
    assert conteudo(secoes, "itens")[0]["reacao"] == NAO_INFORMADO  # type: ignore[index]
    assert descartados


def test_evidencia_tem_que_ser_do_lead_e_ter_tamanho_minimo() -> None:
    f = fatos()
    assert tem_lastro("  ACHEI o plano anual caro!  ", f)
    assert not tem_lastro("Tenho o P-1, plano anual", f)  # fala da assistente
    assert tem_lastro("Tenho o P-1, plano anual", f, papel=None)
    assert not tem_lastro("o", f)
    assert not tem_lastro(None, f)


def test_impressao_digital_muda_com_fatos_relevantes() -> None:
    base = fatos()
    assert base.impressao_digital() == replace(base).impressao_digital()
    outra_ficha = replace(base, ficha={**base.ficha, "orcamento": 150})
    assert outra_ficha.impressao_digital() != base.impressao_digital()
    nova_fala = replace(
        base, mensagens=(*base.mensagens, Mensagem.nova(uuid4(), Papel.LEAD, "e aí?"))
    )
    assert nova_fala.impressao_digital() != base.impressao_digital()
    sem_itens = replace(base, itens={"X": ItemCitado("X", "x")})
    assert sem_itens.impressao_digital() != base.impressao_digital()


# ---------------------------------------------------------------- caso de uso


class Cenario:
    def __init__(
        self,
        *rascunhos: dict[str, object],
        checagens: dict[str, list[AfirmacaoChecada]] | None = None,
    ) -> None:
        self.leads = LeadRepositoryFake()
        self.conversas = ConversaRepositoryFake()
        self.eventos = LeadEventoRepositoryFake()
        self.resumos = ResumoRepositoryFake()
        self.redator = RedatorRoteirizado(*rascunhos, checagens=checagens)
        self.crm = CRMFake()
        self.agenda = AgendaFake([], [])
        self.gerar = GerarResumoHandoff(
            self.leads,
            self.conversas,
            self.eventos,
            resumos=self.resumos,
            redator=self.redator,
            crm=self.crm,
            agenda=self.agenda,
            relogio=RelogioFake(AGORA),
            template=TEMPLATE,
            intencoes=INTENCOES,
        )
        base = Lead.novo(Canal.WEB, "lead-resumo")
        self.lead = replace(
            base,
            qualificacao=Qualificacao(
                base.id,
                intencao_atual="plano",
                fichas={"plano": {"unidade": "Centro", "horario": "noite", "orcamento": 150}},
                score=Score(75, Classificacao.QUENTE, ("3 campos",)),
                proxima_acao="agendar_aula",
            ),
        )
        self.leads.leads[self.lead.id] = self.lead
        self.conversa = Conversa.nova(self.lead)
        self.conversas.conversas[self.conversa.id] = self.conversa
        self.conversas.mensagens.extend(conversa(self.conversa.id))

    def evento(self, tipo: TipoEvento) -> EventoLead:
        return EventoLead(self.lead.id, tipo, AGORA)


async def test_lead_qualificado_gera_v1_e_registra_no_crm() -> None:
    c = Cenario({"perfil": "Quer treinar à noite no Centro."})

    await c.gerar.ao_publicar(
        [c.evento(TipoEvento.SCORE_ALTERADO), c.evento(TipoEvento.LEAD_QUALIFICADO)]
    )

    resumo = await c.resumos.ultimo(c.lead.id)
    assert resumo is not None
    assert (resumo.versao, resumo.gatilho, resumo.titulo) == (1, "LeadQualificado", TEMPLATE.titulo)
    assert resumo.secao("perfil").conteudo == "Quer treinar à noite no Centro."  # type: ignore[union-attr]
    assert c.crm.registros[c.lead.id][1] == resumo
    gerado = [e for e in c.eventos.eventos if e.tipo is TipoEvento.RESUMO_GERADO]
    assert len(gerado) == 1
    assert gerado[0].payload["versao"] == 1
    assert len(c.redator.fatos[0].mensagens) == 4


async def test_mesmos_fatos_nao_geram_nova_versao() -> None:
    c = Cenario({}, {})
    await c.gerar.ao_publicar([c.evento(TipoEvento.LEAD_QUALIFICADO)])
    await c.gerar.ao_publicar([c.evento(TipoEvento.LEAD_QUALIFICADO)])

    assert await c.resumos.versoes(c.lead.id) == [1]
    assert c.crm.chamadas == 1
    assert len(c.redator.fatos) == 1  # nem chamou o LLM de novo


async def test_agendamento_criado_gera_nova_versao_com_o_agendamento() -> None:
    c = Cenario({}, {})
    await c.gerar.ao_publicar([c.evento(TipoEvento.LEAD_QUALIFICADO)])
    sol = Responsavel(uuid4(), "Sol", "consultora")
    agendamento = Agendamento(
        uuid4(), c.lead.id, sol, uuid4(), AGORA + timedelta(days=1),
        AGORA + timedelta(days=1, hours=1), "aula", "presencial", StatusAgendamento.ATIVO, AGORA,
    )  # fmt: skip
    c.agenda.agendamentos[agendamento.id] = agendamento

    await c.gerar.ao_publicar([c.evento(TipoEvento.AGENDAMENTO_CRIADO)])

    resumo = await c.resumos.ultimo(c.lead.id)
    assert resumo is not None
    assert (resumo.versao, resumo.gatilho) == (2, "AgendamentoCriado")
    assert resumo.secao("agendamento").conteudo["responsavel"] == "Sol"  # type: ignore[union-attr,index]
    assert c.crm.registros[c.lead.id][2] == agendamento


async def test_atualizacao_so_regenera_se_ja_existe_resumo() -> None:
    c = Cenario({}, {})
    await c.gerar.ao_publicar([c.evento(TipoEvento.CAMPO_CORRIGIDO)])
    assert c.resumos.resumos == []

    await c.gerar.ao_publicar([c.evento(TipoEvento.LEAD_QUALIFICADO)])
    c.leads.leads[c.lead.id] = replace(
        c.lead,
        qualificacao=replace(
            c.lead.qualificacao, fichas={"plano": {"unidade": "Sul", "horario": "noite"}}
        ),
    )
    await c.gerar.ao_publicar([c.evento(TipoEvento.CAMPO_CORRIGIDO)])

    assert await c.resumos.versoes(c.lead.id) == [1, 2]
    v2 = await c.resumos.ultimo(c.lead.id)
    assert v2 is not None
    assert v2.secao("ficha").conteudo["unidade"] == "Sul"  # type: ignore[union-attr,index]


async def test_eventos_sem_gatilho_nao_geram_resumo() -> None:
    c = Cenario()
    await c.gerar.ao_publicar([c.evento(TipoEvento.SCORE_ALTERADO)])
    assert c.resumos.resumos == []
    assert c.redator.fatos == []


# ---------------------------------------------------------------- checagem de texto livre


def test_podar_textos_mantem_so_frases_sustentadas() -> None:
    original = (
        "Quer treinar no Centro à noite. Levar o P-1, que ele preferiu. "
        "Oferecer aula experimental. É atleta profissional."
    )
    secoes, _ = ancorar({"perfil": original}, TEMPLATE, fatos())
    checagem = {
        "perfil": [
            AfirmacaoChecada(
                "Quer treinar no Centro à noite.",
                True,
                FonteAfirmacao.CONVERSA,
                ("Quero treinar na unidade Centro", "à noite"),
            ),
            AfirmacaoChecada("Levar o P-1, que ele preferiu.", False, FonteAfirmacao.CONVERSA),
            AfirmacaoChecada("Oferecer aula experimental.", True, FonteAfirmacao.RECOMENDACAO),
            # "sustentada", mas evidência que ninguém disse
            AfirmacaoChecada("É atleta profissional.", True, FonteAfirmacao.CONVERSA, "sou atleta"),
        ]
    }

    podadas, descartados = podar_textos(secoes, checagem, fatos())

    assert conteudo(podadas, "perfil") == (
        "Quer treinar no Centro à noite. Oferecer aula experimental."
    )
    assert len(descartados) == 2


def test_podar_textos_nao_aceita_frase_reescrita_nem_secao_sem_checagem() -> None:
    secoes, _ = ancorar({"perfil": "Quer treinar à noite."}, TEMPLATE, fatos())
    reescrita = {"perfil": [AfirmacaoChecada("Quer MUITO treinar.", True, FonteAfirmacao.DADOS)]}
    assert conteudo(podar_textos(secoes, reescrita, fatos())[0], "perfil") == NAO_INFORMADO
    assert conteudo(podar_textos(secoes, {}, fatos())[0], "perfil") == NAO_INFORMADO


async def test_caso_de_uso_poda_frase_sem_sustentacao_antes_de_salvar_e_enviar_ao_crm() -> None:
    texto = "Levar o P-1. Ele preferiu o P-1."
    c = Cenario(
        {"perfil": texto},
        checagens={
            "perfil": [
                AfirmacaoChecada("Levar o P-1.", True, FonteAfirmacao.RECOMENDACAO),
                AfirmacaoChecada("Ele preferiu o P-1.", False, FonteAfirmacao.CONVERSA),
            ]
        },
    )

    await c.gerar.ao_publicar([c.evento(TipoEvento.LEAD_QUALIFICADO)])

    assert c.redator.checados == [{"perfil": texto}]
    resumo = c.crm.registros[c.lead.id][1]
    assert resumo.secao("perfil").conteudo == "Levar o P-1."  # type: ignore[union-attr]
    assert any("preferiu" in d for d in resumo.descartados)


async def test_handoff_confirmado_gera_resumo_com_as_mensagens_da_fila() -> None:
    c = Cenario({})
    fila_desde = c.conversas.mensagens[-1].criada_em + timedelta(seconds=1)
    c.leads.leads[c.lead.id] = replace(
        c.lead,
        atendimento=Atendimento(
            c.lead.id, EstadoAtendimento.AGUARDANDO_HUMANO, na_fila_desde=fila_desde
        ),
    )
    na_fila = Mensagem.nova(
        c.conversa.id, Papel.LEAD, "oi? alguém aí?", criada_em=fila_desde + timedelta(minutes=2)
    )
    c.conversas.mensagens.append(na_fila)

    await c.gerar.ao_publicar([c.evento(TipoEvento.HANDOFF_CONFIRMADO)])

    resumo = await c.resumos.ultimo(c.lead.id)
    assert resumo is not None
    assert resumo.gatilho == "HandoffConfirmado"
    anexo = resumo.secao("mensagens_na_espera")
    assert anexo is not None
    assert anexo.conteudo == ["oi? alguém aí?"]  # só o que veio depois de entrar na fila
