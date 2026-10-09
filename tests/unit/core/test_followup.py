"""Follow-up: domínio (elegibilidade, SLA) e casos de uso com relógio fake, sem banco/LLM."""

from dataclasses import replace
from datetime import datetime, timedelta
from uuid import uuid4

import pytest

from sdr.core.application.dto.mensagem_recebida import MensagemRecebida
from sdr.core.application.use_cases.followup import (
    ConfigFollowUp,
    ExecutarFollowUps,
    ProgramarFollowUps,
)
from sdr.core.application.use_cases.processar_turno import ProcessarTurno
from sdr.core.application.use_cases.receber_mensagem import ReceberMensagem
from sdr.core.domain.agenda import Agendamento, Responsavel, StatusAgendamento
from sdr.core.domain.agente import Persona, RespostaAgente
from sdr.core.domain.atendimento import Atendimento, EstadoAtendimento, HorarioAtendimento
from sdr.core.domain.catalogo import ConsultaCatalogo
from sdr.core.domain.conversa import (
    Canal,
    Conversa,
    Lead,
    Mensagem,
    Papel,
    StatusEntrega,
    StatusMensagem,
)
from sdr.core.domain.eventos import EventoLead, TipoEvento
from sdr.core.domain.followup import (
    Cadencia,
    ContextoElegibilidade,
    EtapaCadencia,
    FollowUp,
    ModeloLembrete,
    MotivoInelegivel,
    SituacaoLead,
    StatusFollowUp,
    TipoFollowUp,
    UnidadeTempo,
    cadencias_validas,
    motivo_inelegivel,
    proxima_checagem_sla,
    requer_template,
    situacao_do_lead,
    tempo_util,
)
from sdr.core.domain.qualificacao import Qualificacao
from tests.apoio.fakes import (
    FUSO_SP,
    AgendadorFake,
    AgendaFake,
    AgenteRoteirizado,
    CanalFake,
    CatalogoFake,
    ConversaRepositoryFake,
    FollowUpRepositoryFake,
    LeadEventoRepositoryFake,
    LeadRepositoryFake,
    RedatorMensagemAtivaFake,
    RelogioFake,
    TravaFake,
    entrega_fake,
    item,
    template_teste,
)

E = EstadoAtendimento
HORARIO = HorarioAtendimento.de_texto("seg-sex", "09:00-18:00", "America/Sao_Paulo")
SEGUNDA_10H = datetime(2026, 10, 5, 10, tzinfo=FUSO_SP)
CADENCIA = Cadencia(
    (
        EtapaCadencia(1, "retomar", template=template_teste("t_retomar")),
        EtapaCadencia(2, "trazer novidade", template=template_teste("t_novidade")),
        EtapaCadencia(4, "encerrar", template=template_teste("t_encerrar"), encerramento=True),
    )
)
CADENCIAS = {s: CADENCIA for s in SituacaoLead}
PERSONA = Persona("Bia", "v1", "PERSONA", padrao_codigo_item=r"\bA-\d+\b")
LEMBRETE = ModeloLembrete(
    "lembrar da aula e pedir confirmação", template=template_teste("t_lembrete")
)


# ---------------------------------------------------------------- domínio


def ctx(**sobrescritas: object) -> ContextoElegibilidade:
    base = ContextoElegibilidade(
        atendimento=Atendimento(uuid4()),
        opt_out=False,
        conversa_aberta=True,
        tem_agendamento_futuro=False,
        lead_falou_por_ultimo_em=SEGUNDA_10H - timedelta(hours=1),
    )
    return replace(base, **sobrescritas)  # type: ignore[arg-type]


def retomada(**extra: object) -> FollowUp:
    return FollowUp.novo(uuid4(), TipoFollowUp.RETOMADA, SEGUNDA_10H, SEGUNDA_10H, **extra)


@pytest.mark.parametrize(
    ("contexto", "motivo"),
    [
        (ctx(), None),
        (ctx(opt_out=True), MotivoInelegivel.OPT_OUT),
        (ctx(conversa_aberta=False), MotivoInelegivel.CONVERSA_ENCERRADA),
        (
            ctx(atendimento=Atendimento(uuid4(), E.AGUARDANDO_HUMANO)),
            MotivoInelegivel.FORA_DA_IA,
        ),
        (
            ctx(lead_falou_por_ultimo_em=SEGUNDA_10H + timedelta(minutes=1)),
            MotivoInelegivel.LEAD_RESPONDEU,
        ),
        (ctx(tem_agendamento_futuro=True), MotivoInelegivel.AGENDAMENTO_FUTURO),
    ],
)
def test_elegibilidade(contexto: ContextoElegibilidade, motivo: MotivoInelegivel | None) -> None:
    agora = SEGUNDA_10H + timedelta(days=1)
    assert motivo_inelegivel(retomada(), contexto, agora, HORARIO) is motivo


def test_fora_do_horario_adia_a_menos_que_desligado_ou_demo() -> None:
    sabado = datetime(2026, 10, 10, 11, tzinfo=FUSO_SP)
    assert motivo_inelegivel(retomada(), ctx(), sabado, HORARIO) is MotivoInelegivel.FORA_DO_HORARIO
    assert motivo_inelegivel(retomada(), ctx(), sabado, HORARIO, respeitar_horario=False) is None
    assert motivo_inelegivel(retomada(ignorar_horario=True), ctx(), sabado, HORARIO) is None


def test_lembrete_nao_e_cancelado_por_resposta_nem_por_agendamento() -> None:
    lembrete = FollowUp.novo(uuid4(), TipoFollowUp.LEMBRETE_AGENDAMENTO, SEGUNDA_10H, SEGUNDA_10H)
    contexto = ctx(
        tem_agendamento_futuro=True, lead_falou_por_ultimo_em=SEGUNDA_10H + timedelta(hours=2)
    )
    assert motivo_inelegivel(lembrete, contexto, SEGUNDA_10H + timedelta(hours=3), HORARIO) is None


def test_requer_template_fora_da_janela_de_24h() -> None:
    janela = timedelta(hours=24)
    assert not requer_template(SEGUNDA_10H, SEGUNDA_10H + timedelta(hours=23), janela)
    assert requer_template(SEGUNDA_10H, SEGUNDA_10H + timedelta(hours=25), janela)
    assert requer_template(None, SEGUNDA_10H, janela)


def test_tempo_util_conta_so_o_horario_de_atendimento() -> None:
    sexta_17h = datetime(2026, 10, 9, 17, tzinfo=FUSO_SP)
    segunda_9h30 = datetime(2026, 10, 12, 9, 30, tzinfo=FUSO_SP)
    assert tempo_util(sexta_17h, segunda_9h30, HORARIO) == timedelta(minutes=90)


def test_sla_na_fila_so_estoura_em_tempo_util() -> None:
    sla = timedelta(minutes=15)
    sexta_17h55 = datetime(2026, 10, 9, 17, 55, tzinfo=FUSO_SP)
    sexta_19h = datetime(2026, 10, 9, 19, tzinfo=FUSO_SP)
    # 5 min úteis na sexta; faltam 10: checar segunda 9h10
    assert proxima_checagem_sla(sexta_17h55, sla, sexta_19h, HORARIO) == datetime(
        2026, 10, 12, 9, 10, tzinfo=FUSO_SP
    )
    segunda_9h11 = datetime(2026, 10, 12, 9, 11, tzinfo=FUSO_SP)
    assert proxima_checagem_sla(sexta_17h55, sla, segunda_9h11, HORARIO) is None


def test_situacao_e_unidade() -> None:
    lead_id = uuid4()
    assert situacao_do_lead(Qualificacao(lead_id)) is SituacaoLead.DESCOBERTA
    assert situacao_do_lead(Qualificacao(lead_id, "plano")) is SituacaoLead.QUALIFICACAO
    assert (
        situacao_do_lead(Qualificacao(lead_id, "plano", proxima_acao="x"))
        is SituacaoLead.QUALIFICADO
    )
    assert UnidadeTempo.MINUTOS.duracao(3) == timedelta(minutes=3)
    assert UnidadeTempo.DIAS.duracao(3) == timedelta(days=3)
    with pytest.raises(ValueError, match="encerramento"):
        cadencias_validas(
            {SituacaoLead.DESCOBERTA: Cadencia((EtapaCadencia(1, "x", template_teste("t")),))}
        )


# ---------------------------------------------------------------- casos de uso


class Cenario:
    def __init__(
        self,
        *textos: str,
        agora: datetime = SEGUNDA_10H,
        lembrete: ModeloLembrete | None = LEMBRETE,
        canal: CanalFake | None = None,
    ) -> None:
        self.relogio = RelogioFake(agora)
        self.leads = LeadRepositoryFake()
        self.conversas = ConversaRepositoryFake()
        self.eventos = LeadEventoRepositoryFake()
        self.followups = FollowUpRepositoryFake()
        self.agenda = AgendaFake([], [])
        self.canal = canal or CanalFake()
        self.catalogo = CatalogoFake(item("A-1"), item("A-2"))
        self.redator = RedatorMensagemAtivaFake(*textos)
        config = ConfigFollowUp(cadencias=CADENCIAS, horario=HORARIO, lembrete=lembrete)
        self.programar = ProgramarFollowUps(
            self.followups,
            self.leads,
            self.conversas,
            eventos=self.eventos,
            agenda=self.agenda,
            relogio=self.relogio,
            config=config,
        )
        self.executar = ExecutarFollowUps(
            self.followups,
            self.programar,
            self.leads,
            conversas=self.conversas,
            eventos=self.eventos,
            agenda=self.agenda,
            catalogo=self.catalogo,
            redator=self.redator,
            entrega=entrega_fake(self.conversas, self.eventos, self.canal, self.relogio),
            relogio=self.relogio,
            persona=PERSONA,
            config=config,
            consulta_para_ficha=lambda intencao, ficha: ConsultaCatalogo(texto="x"),
        )
        base = Lead.novo(Canal.WEB, "lead-sumiu")
        self.lead = replace(
            base,
            qualificacao=Qualificacao(base.id, "plano", fichas={"plano": {"unidade": "Centro"}}),
        )
        self.leads.leads[self.lead.id] = self.lead
        self.conversa = Conversa.nova(self.lead)
        self.conversas.conversas[self.conversa.id] = self.conversa
        self.fala(Papel.LEAD, "quero um plano no Centro")
        citados = {"itens_citados": [{"id": "A-1", "titulo": "Plano A-1"}]}
        self.fala(Papel.ASSISTENTE, "Tenho o A-1! Qual horário?", metadados=citados)

    def fala(self, papel: Papel, texto: str, **extra: object) -> None:
        self.conversas.mensagens.append(
            Mensagem.nova(self.conversa.id, papel, texto, criada_em=self.relogio.agora(), **extra)  # type: ignore[arg-type]
        )

    def tipos(self) -> list[TipoEvento]:
        return [e.tipo for e in self.eventos.eventos]

    def enviadas(self) -> list[Mensagem]:
        """Mensagens ativas gravadas na conversa (pelo canal ou por template)."""
        return [m for m in self.conversas.mensagens if "followup" in m.metadados]


async def test_resposta_do_assistente_programa_a_etapa_1() -> None:
    c = Cenario()
    await c.programar.apos_resposta(c.lead)

    [f] = c.followups.do_lead(c.lead.id)
    assert (f.etapa, f.situacao, f.executar_em) == (
        1,
        SituacaoLead.QUALIFICACAO,
        SEGUNDA_10H + timedelta(days=1),
    )
    assert c.tipos() == [TipoEvento.FOLLOWUP_AGENDADO]

    await c.programar.apos_resposta(c.lead)  # nova resposta: recomeça, sem duplicar
    pendentes = await c.followups.pendentes(c.lead.id)
    assert len(pendentes) == 1


async def test_sem_follow_up_fora_da_ia_ou_com_opt_out() -> None:
    c = Cenario()
    na_fila = replace(c.lead, atendimento=Atendimento(c.lead.id, E.AGUARDANDO_HUMANO))
    assert await c.programar.apos_resposta(na_fila) == []
    assert await c.programar.apos_resposta(replace(c.lead, opt_out_em=SEGUNDA_10H)) == []
    assert c.followups.itens == {}


async def test_cadencia_completa_retoma_traz_item_novo_e_encerra() -> None:
    c = Cenario("Oi! Lembra do plano? Tenho o A-2 também.", "Novidade!", "Até mais!")
    await c.programar.apos_resposta(c.lead)

    for dias in (1, 2, 4):  # D+1, D+3, D+7
        c.relogio.avancar(days=dias)
        assert await c.executar.executar() == 1

    assert [m.metadados["followup"]["etapa"] for m in c.enviadas()] == [1, 2, 3]  # type: ignore[index]
    primeiro = c.redator.pedidos[0]
    assert [i.id for i in primeiro.itens_novos] == ["A-2"]  # A-1 já tinha sido apresentado
    assert "quero um plano no Centro" in [m.texto for m in primeiro.historico]
    assert c.redator.pedidos[-1].encerramento is True
    assert c.redator.pedidos[-1].itens_novos == []
    assert c.tipos().count(TipoEvento.FOLLOWUP_ENVIADO) == 3
    assert c.tipos()[-1] is TipoEvento.LEAD_ENCERRADO_INATIVIDADE
    assert await c.followups.pendentes(c.lead.id) == []  # cadência acabou
    assert c.enviadas()[0].metadados["itens_citados"][0]["id"] == "A-2"  # type: ignore[index]


async def test_lead_respondeu_cancela_os_pendentes_e_reengaja() -> None:
    c = Cenario("Oi! Ainda procurando?")
    await c.programar.apos_resposta(c.lead)
    c.relogio.avancar(days=1)
    await c.executar.executar()
    c.relogio.avancar(hours=2)

    eventos = await c.programar.lead_respondeu(c.lead, c.conversa)

    assert [e.tipo for e in eventos] == [TipoEvento.LEAD_REENGAJADO]
    assert eventos[0].payload == {"etapa": 1}
    assert await c.followups.pendentes(c.lead.id) == []  # a etapa 2 caiu


async def test_resposta_que_chega_antes_do_envio_cancela_no_worker() -> None:
    c = Cenario()
    await c.programar.apos_resposta(c.lead)
    c.relogio.avancar(hours=20)
    c.fala(Papel.LEAD, "voltei")  # (sem passar pelo ReceberMensagem)
    c.relogio.avancar(hours=5)

    await c.executar.executar()

    [f] = c.followups.do_lead(c.lead.id)
    assert (f.status, f.motivo) == (StatusFollowUp.CANCELADO, "lead_respondeu")
    assert c.enviadas() == []


async def test_fora_do_horario_adia_para_a_proxima_abertura() -> None:
    sexta_19h = datetime(2026, 10, 9, 19, tzinfo=FUSO_SP)
    c = Cenario(agora=sexta_19h - timedelta(days=1))
    await c.programar.apos_resposta(c.lead)
    c.relogio.momento = sexta_19h

    await c.executar.executar()

    [f] = await c.followups.pendentes(c.lead.id)
    assert (f.executar_em, f.motivo) == (
        datetime(2026, 10, 12, 9, tzinfo=FUSO_SP),
        "fora_do_horario",
    )
    assert c.enviadas() == []


async def test_agendamento_futuro_cancela_a_retomada() -> None:
    c = Cenario()
    await c.programar.apos_resposta(c.lead)
    inicio = SEGUNDA_10H + timedelta(days=3)
    c.agenda.agendamentos[uuid4()] = Agendamento(
        uuid4(), c.lead.id, Responsavel(uuid4(), "Sol", "x"), uuid4(), inicio,
        inicio + timedelta(hours=1), "aula", "presencial", StatusAgendamento.ATIVO, SEGUNDA_10H,
    )  # fmt: skip
    c.relogio.avancar(days=1)

    await c.executar.executar()

    [f] = c.followups.do_lead(c.lead.id)
    assert f.motivo == "tem_agendamento_futuro"


async def test_fora_da_janela_de_24h_sai_so_por_template_com_ganchos_validados() -> None:
    c = Cenario("Lembrei da\nsua busca.")  # gancho com quebra de linha: o validador recusa
    await c.programar.apos_resposta(c.lead)
    c.relogio.avancar(days=1, hours=1)
    await c.executar.executar()

    [enviada] = c.enviadas()
    assert enviada.metadados["requer_template"] is True
    assert c.canal.enviadas == []  # nada de texto livre fora da janela
    [(_, nome, variaveis)] = c.canal.templates
    assert nome == "t_retomar"  # o template lógico da etapa
    assert variaveis == {"primeiro_nome": "tudo bem", "gancho": "Lembrei de você."}  # fallbacks
    assert enviada.texto == "Oi, tudo bem! Lembrei de você. Responda quando puder."
    assert enviada.metadados["envio"]["fallbacks"] == ["primeiro_nome", "gancho"]  # type: ignore[index]
    assert enviada.entrega is StatusEntrega.ENVIADA


async def test_gancho_valido_do_llm_entra_no_template() -> None:
    c = Cenario("Ainda pensando no plano do Centro?")
    await c.programar.apos_resposta(c.lead)
    c.relogio.avancar(days=2)
    await c.executar.executar()
    [(_, _, variaveis)] = c.canal.templates
    assert variaveis["gancho"] == "Ainda pensando no plano do Centro?"


async def test_dentro_da_janela_sai_texto_livre() -> None:
    c = Cenario("Oi! Ainda quer o plano no Centro?")
    await c.programar.apos_resposta(c.lead)
    c.relogio.avancar(hours=23)  # antecipa a etapa 1 para 23h depois da fala do lead
    c.followups.itens = {
        k: replace(f, executar_em=c.relogio.agora()) for k, f in c.followups.itens.items()
    }
    await c.executar.executar()
    [(_, mensagem)] = c.canal.enviadas
    assert mensagem.texto == "Oi! Ainda quer o plano no Centro?"
    assert c.canal.templates == []


async def test_template_sem_mapeamento_no_provedor_nao_envia_e_emite_evento() -> None:
    c = Cenario()
    c.canal = CanalFake(templates_mapeados=set())
    c.executar._entrega = entrega_fake(c.conversas, c.eventos, c.canal, c.relogio)
    await c.programar.apos_resposta(c.lead)
    c.relogio.avancar(days=2)
    await c.executar.executar()

    [enviada] = c.enviadas()
    assert enviada.status is StatusMensagem.NAO_ENVIADA  # fora da memória do agente
    assert enviada.entrega is StatusEntrega.FALHOU
    assert c.canal.enviadas == []
    assert c.canal.templates == []
    [evento] = [e for e in c.eventos.eventos if e.tipo is TipoEvento.ENVIO_TEMPLATE_INDISPONIVEL]
    assert evento.payload["template"] == "t_retomar"
    [enviado] = [e for e in c.eventos.eventos if e.tipo is TipoEvento.FOLLOWUP_ENVIADO]
    assert enviado.payload["entrega"] == "falhou"


async def test_codigo_inventado_pede_correcao_ao_redator() -> None:
    c = Cenario("Olha o A-99!", "Olha o A-2!")
    await c.programar.apos_resposta(c.lead)
    c.relogio.avancar(days=1)
    await c.executar.executar()
    assert c.enviadas()[0].texto == "Olha o A-2!"
    assert "A-99" in (c.redator.pedidos[1].instrucao_adicional or "")


async def test_opt_out_cancela_tudo_e_registra() -> None:
    c = Cenario()
    await c.programar.apos_resposta(c.lead)
    eventos = await c.programar.registrar_opt_out(c.lead, "pare de mandar mensagem")

    assert [e.tipo for e in eventos] == [TipoEvento.LEAD_OPT_OUT]
    assert eventos[0].payload["followups_cancelados"] == 1
    assert c.leads.leads[c.lead.id].opt_out_em == SEGUNDA_10H
    assert await c.followups.pendentes(c.lead.id) == []


async def test_eventos_programam_lembrete_e_sla() -> None:
    c = Cenario()
    inicio = SEGUNDA_10H + timedelta(days=2)
    ag_id = uuid4()
    await c.programar.ao_publicar(
        [
            EventoLead(
                c.lead.id,
                TipoEvento.AGENDAMENTO_CRIADO,
                SEGUNDA_10H,
                {"agendamento_id": str(ag_id), "inicio": inicio.isoformat()},
            )
        ]
    )
    [lembrete] = c.followups.do_lead(c.lead.id, TipoFollowUp.LEMBRETE_AGENDAMENTO)
    assert (lembrete.executar_em, lembrete.referencia) == (inicio - timedelta(hours=24), str(ag_id))

    na_fila = Atendimento(c.lead.id, E.AGUARDANDO_HUMANO, SEGUNDA_10H, na_fila_desde=SEGUNDA_10H)
    c.leads.leads[c.lead.id] = replace(c.lead, atendimento=na_fila)
    await c.programar.ao_publicar(
        [EventoLead(c.lead.id, TipoEvento.HANDOFF_CONFIRMADO, SEGUNDA_10H)]
    )
    [sla] = c.followups.do_lead(c.lead.id, TipoFollowUp.SLA_HANDOFF)
    assert sla.executar_em == SEGUNDA_10H + timedelta(minutes=15)

    await c.programar.ao_publicar(
        [EventoLead(c.lead.id, TipoEvento.ATENDIMENTO_HUMANO_INICIADO, SEGUNDA_10H)]
    )
    assert c.followups.do_lead(c.lead.id, TipoFollowUp.SLA_HANDOFF)[0].status is (
        StatusFollowUp.CANCELADO
    )


async def test_sla_estourado_na_fila_emite_alerta() -> None:
    c = Cenario()
    na_fila = Atendimento(c.lead.id, E.AGUARDANDO_HUMANO, SEGUNDA_10H, na_fila_desde=SEGUNDA_10H)
    c.leads.leads[c.lead.id] = replace(c.lead, atendimento=na_fila)
    await c.programar.ao_publicar(
        [EventoLead(c.lead.id, TipoEvento.HANDOFF_CONFIRMADO, SEGUNDA_10H)]
    )
    c.relogio.avancar(minutes=16)

    await c.executar.executar()

    assert c.tipos()[-1] is TipoEvento.HANDOFF_SLA_EXCEDIDO
    assert c.eventos.eventos[-1].payload["espera_util_minutos"] == 16
    assert c.enviadas() == []  # é alerta para a equipe, não mensagem ao lead


async def test_lembrete_sai_na_hora_e_cancela_se_o_agendamento_mudou() -> None:
    c = Cenario("Lembrete: amanhã às 10h!")
    inicio = SEGUNDA_10H + timedelta(days=1, hours=1)
    ag = Agendamento(
        uuid4(), c.lead.id, Responsavel(uuid4(), "Sol", "x"), uuid4(), inicio,
        inicio + timedelta(hours=1), "aula", "presencial", StatusAgendamento.ATIVO, SEGUNDA_10H,
    )  # fmt: skip
    c.agenda.agendamentos[ag.id] = ag
    await c.programar.ao_publicar(
        [
            EventoLead(
                c.lead.id,
                TipoEvento.AGENDAMENTO_CRIADO,
                SEGUNDA_10H,
                {"agendamento_id": str(ag.id), "inicio": inicio.isoformat()},
            )
        ]
    )
    c.relogio.avancar(hours=1)

    await c.executar.executar()

    assert [m.texto for m in c.enviadas()] == ["Lembrete: amanhã às 10h!"]
    assert c.redator.pedidos[0].agendamento == ag
    assert c.redator.pedidos[0].objetivo == LEMBRETE.objetivo  # texto da vertical, não do core


async def test_simular_inatividade_antecipa_o_proximo_e_ignora_o_expediente() -> None:
    sabado = datetime(2026, 10, 10, 11, tzinfo=FUSO_SP)
    c = Cenario("Oi de novo!", agora=sabado)
    await c.programar.apos_resposta(c.lead)

    antecipado = await c.programar.simular_inatividade(Canal.WEB, "lead-sumiu")
    await c.executar.executar()

    assert antecipado is not None
    assert antecipado.executar_em == sabado
    assert [m.texto for m in c.enviadas()] == ["Oi de novo!"]
    [proximo] = await c.followups.pendentes(c.lead.id)
    assert (proximo.etapa, proximo.ignorar_horario) == (2, True)  # demo segue sem expediente


# ---------------------------------------------------------------- integração com a conversa


async def test_turno_reprograma_a_cadencia_e_opt_out_encerra() -> None:
    c = Cenario()
    agente = AgenteRoteirizado(
        RespostaAgente("Qual horário?"), RespostaAgente("Tudo bem!", opt_out=True)
    )
    turno = ProcessarTurno(
        c.leads,
        c.conversas,
        agente,
        c.catalogo,
        eventos=c.eventos,
        persona=PERSONA,
        trava=TravaFake(),
        agendador=AgendadorFake(),
        entrega=entrega_fake(c.conversas, c.eventos, c.canal, c.relogio),
        followups=c.programar,
    )

    c.conversas.mensagens.append(
        Mensagem.nova(c.conversa.id, Papel.LEAD, "oi", status=StatusMensagem.PENDENTE)
    )
    await turno.executar(c.lead.id)
    assert len(await c.followups.pendentes(c.lead.id)) == 1

    c.conversas.mensagens.append(
        Mensagem.nova(c.conversa.id, Papel.LEAD, "pare de mandar", status=StatusMensagem.PENDENTE)
    )
    await turno.executar(c.lead.id)

    assert await c.followups.pendentes(c.lead.id) == []
    assert c.leads.leads[c.lead.id].opt_out_em is not None
    assert c.tipos()[-1] is TipoEvento.LEAD_OPT_OUT
    assert c.eventos.eventos[-1].payload["fala"] == "pare de mandar"


async def test_receber_mensagem_cancela_pendentes() -> None:

    c = Cenario()
    await c.programar.apos_resposta(c.lead)
    receber = ReceberMensagem(
        c.leads, c.conversas, c.eventos, AgendadorFake(), followups=c.programar
    )
    await receber.executar(MensagemRecebida(Canal.WEB, "lead-sumiu", "voltei!", SEGUNDA_10H))

    assert await c.followups.pendentes(c.lead.id) == []


async def test_sem_modelo_de_lembrete_na_vertical_nao_ha_lembrete() -> None:
    c = Cenario(lembrete=None)
    inicio = SEGUNDA_10H + timedelta(days=2)
    await c.programar.ao_publicar(
        [
            EventoLead(
                c.lead.id,
                TipoEvento.AGENDAMENTO_CRIADO,
                SEGUNDA_10H,
                {"agendamento_id": str(uuid4()), "inicio": inicio.isoformat()},
            )
        ]
    )
    assert c.followups.do_lead(c.lead.id, TipoFollowUp.LEMBRETE_AGENDAMENTO) == []
