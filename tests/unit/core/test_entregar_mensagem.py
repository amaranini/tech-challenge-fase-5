"""Decisão texto livre × template pela janela de 24h (core) e mídia no turno."""

from sdr.core.application.use_cases.processar_turno import ProcessarTurno
from sdr.core.domain.agente import Persona, RespostaAgente
from sdr.core.domain.conversa import (
    Canal,
    Conversa,
    Lead,
    Mensagem,
    Papel,
    StatusEntrega,
    StatusMensagem,
)
from sdr.core.domain.eventos import TipoEvento
from tests.apoio.fakes import (
    AgendadorFake,
    AgenteRoteirizado,
    CanalFake,
    CatalogoFake,
    ConversaRepositoryFake,
    LeadEventoRepositoryFake,
    LeadRepositoryFake,
    RelogioFake,
    TravaFake,
    entrega_fake,
    template_teste,
)
from tests.unit.core.test_followup import SEGUNDA_10H

TEMPLATE = template_teste("retomada")


class Cenario:
    def __init__(self, canal: CanalFake | None = None) -> None:
        self.relogio = RelogioFake(SEGUNDA_10H)
        self.conversas = ConversaRepositoryFake()
        self.eventos = LeadEventoRepositoryFake()
        self.canal = canal or CanalFake()
        self.entrega = entrega_fake(self.conversas, self.eventos, self.canal, self.relogio)
        self.lead = Lead.novo(Canal.WHATSAPP, "+5511987654321", "Ana")
        self.conversa = Conversa.nova(self.lead)
        self.conversas.conversas[self.conversa.id] = self.conversa

    def lead_fala(self, texto: str = "oi") -> None:
        self.conversas.mensagens.append(
            Mensagem.nova(self.conversa.id, Papel.LEAD, texto, criada_em=self.relogio.agora())
        )

    async def enviar(self, texto: str = "Oi, Ana! Tudo certo?") -> Mensagem:
        mensagem = await self.entrega.preparar(
            self.lead, self.conversa, Papel.ASSISTENTE, texto, {}, template=TEMPLATE
        )
        await self.conversas.adicionar_mensagem(mensagem)
        await self.entrega.transmitir(self.lead, mensagem)
        return self.conversas.mensagem(mensagem.id)


async def test_dentro_da_janela_sai_texto_livre() -> None:
    c = Cenario()
    c.lead_fala()
    c.relogio.avancar(hours=23, minutes=59)
    enviada = await c.enviar()
    assert [m.texto for _, m in c.canal.enviadas] == ["Oi, Ana! Tudo certo?"]
    assert c.canal.templates == []
    assert enviada.metadados["envio"] == {"modo": "texto"}
    assert enviada.entrega is StatusEntrega.ENVIADA


async def test_a_janela_conta_da_ultima_mensagem_do_lead_nao_da_nossa() -> None:
    c = Cenario()
    c.lead_fala()
    c.relogio.avancar(hours=20)
    await c.enviar("primeira")  # nossa mensagem não renova a janela
    c.relogio.avancar(hours=5)
    await c.enviar("segunda")
    assert [m.texto for _, m in c.canal.enviadas] == ["primeira"]
    [(_, nome, variaveis)] = c.canal.templates
    assert (nome, variaveis["primeiro_nome"]) == ("retomada", "Ana")


async def test_fora_da_janela_sai_template_com_texto_renderizado() -> None:
    c = Cenario()
    c.lead_fala()
    c.relogio.avancar(hours=24, minutes=1)
    enviada = await c.enviar("texto livre que não pode sair")
    assert c.canal.enviadas == []
    assert enviada.texto == "Oi, Ana! Lembrei de você. Responda quando puder."
    assert enviada.metadados["envio"]["texto_original"] == "texto livre que não pode sair"  # type: ignore[index]


async def test_lead_que_nunca_falou_esta_fora_da_janela() -> None:
    c = Cenario()
    await c.enviar()
    assert len(c.canal.templates) == 1


async def test_fora_da_janela_sem_template_logico_nao_envia_livre() -> None:
    c = Cenario()
    c.lead_fala()
    c.relogio.avancar(days=2)
    mensagem = await c.entrega.preparar(c.lead, c.conversa, Papel.RESPONSAVEL, "oi", {})
    await c.conversas.adicionar_mensagem(mensagem)
    status = await c.entrega.transmitir(c.lead, mensagem)

    assert status is StatusEntrega.FALHOU
    assert c.canal.enviadas == []
    assert c.canal.templates == []
    assert c.conversas.mensagem(mensagem.id).status is StatusMensagem.NAO_ENVIADA
    [evento] = c.eventos.eventos
    assert evento.tipo is TipoEvento.ENVIO_TEMPLATE_INDISPONIVEL
    assert evento.payload["motivo"] == "sem_template_logico"


async def test_template_nao_mapeado_no_provedor_vira_evento_e_nao_envia() -> None:
    c = Cenario(CanalFake(templates_mapeados={"outro"}))
    enviada = await c.enviar()
    assert enviada.status is StatusMensagem.NAO_ENVIADA
    assert enviada.entrega is StatusEntrega.FALHOU
    [evento] = c.eventos.eventos
    assert (evento.tipo, evento.payload["template"]) == (
        TipoEvento.ENVIO_TEMPLATE_INDISPONIVEL,
        "retomada",
    )
    assert await c.conversas.ultimas_mensagens(c.conversa.id, 10) == []  # fora da memória


async def test_falha_do_provedor_registra_e_nao_derruba_o_fluxo() -> None:
    c = Cenario(CanalFake(falhar=True))
    c.lead_fala()
    enviada = await c.enviar()
    assert enviada.entrega is StatusEntrega.FALHOU
    assert enviada.status is StatusMensagem.ENVIADA  # saiu do sistema; o provedor recusou
    assert c.eventos.eventos[0].tipo is TipoEvento.MENSAGEM_NAO_ENTREGUE


# ---------------------------------------------------------------- mídia no turno


async def test_lote_so_com_audio_responde_o_aviso_da_persona_sem_chamar_o_agente() -> None:
    leads, conversas, eventos = (
        LeadRepositoryFake(),
        ConversaRepositoryFake(),
        LeadEventoRepositoryFake(),
    )
    canal, agente = CanalFake(), AgenteRoteirizado(RespostaAgente("não devia"))
    persona = Persona("Bia", "v1", "PERSONA", aviso_midia="Só leio texto por enquanto!")
    turno = ProcessarTurno(
        leads, conversas, agente, CatalogoFake(), eventos=eventos, persona=persona,
        trava=TravaFake(), agendador=AgendadorFake(),
        entrega=entrega_fake(conversas, eventos, canal),
    )  # fmt: skip
    lead = Lead.novo(Canal.WHATSAPP, "+5511987654321")
    leads.leads[lead.id] = lead
    conversa = Conversa.nova(lead)
    conversas.conversas[conversa.id] = conversa
    conversas.mensagens.append(
        Mensagem.nova(
            conversa.id, Papel.LEAD, "[áudio]", status=StatusMensagem.PENDENTE,
            metadados={"midias": [{"tipo": "audio"}], "somente_midia": True, "texto_lead": ""},
        )
    )  # fmt: skip

    resultado = await turno.executar(lead.id)

    assert resultado is not None
    assert resultado.resposta.texto == "Só leio texto por enquanto!"
    assert agente.entradas == []
    assert [m.texto for _, m in canal.enviadas] == ["Só leio texto por enquanto!"]


async def test_texto_com_anexo_vai_ao_agente_com_uma_nota_sobre_o_anexo() -> None:
    leads, conversas, eventos = (
        LeadRepositoryFake(),
        ConversaRepositoryFake(),
        LeadEventoRepositoryFake(),
    )
    agente = AgenteRoteirizado(RespostaAgente("Vi sua mensagem!"))
    turno = ProcessarTurno(
        leads, conversas, agente, CatalogoFake(), eventos=eventos, persona=Persona("B", "v", "P"),
        trava=TravaFake(), agendador=AgendadorFake(),
        entrega=entrega_fake(conversas, eventos, CanalFake()),
    )  # fmt: skip
    lead = Lead.novo(Canal.WHATSAPP, "+5511987654321")
    leads.leads[lead.id] = lead
    conversa = Conversa.nova(lead)
    conversas.conversas[conversa.id] = conversa
    conversas.mensagens.append(
        Mensagem.nova(
            conversa.id, Papel.LEAD, "olha essa planta\n[imagem]", status=StatusMensagem.PENDENTE,
            metadados={"midias": [{"tipo": "imagem"}], "somente_midia": False,
                       "texto_lead": "olha essa planta"},
        )
    )  # fmt: skip

    await turno.executar(lead.id)

    [entrada] = agente.entradas
    assert entrada.texto.startswith("olha essa planta\n(o lead também enviou anexo: imagem")
