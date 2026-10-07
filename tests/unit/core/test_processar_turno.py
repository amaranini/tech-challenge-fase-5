"""ProcessarTurno: agregação, reprocessamento, trava, guarda de itens e persistência."""

from sdr.core.application.ports.agente import EntradaAgente
from sdr.core.application.ports.llm import LLMIndisponivelError
from sdr.core.application.use_cases.processar_turno import ProcessarTurno
from sdr.core.domain.agente import Persona, RespostaAgente
from sdr.core.domain.conversa import Canal, Conversa, Lead, Mensagem, Papel, StatusMensagem
from sdr.core.domain.eventos import EventoLead, TipoEvento
from sdr.core.domain.qualificacao import Qualificacao
from tests.apoio.fakes import (
    AgendadorFake,
    AgenteRoteirizado,
    CanalFake,
    CatalogoFake,
    ConversaRepositoryFake,
    LeadEventoRepositoryFake,
    LeadRepositoryFake,
    TravaFake,
    item,
)

PERSONA = Persona(
    nome="Ana",
    versao_prompt="ana_v1",
    prompt_sistema="...",
    padrao_codigo_item=r"\bA-\d+\b",
    mensagem_fallback="Já te respondo!",
)


class Cenario:
    def __init__(
        self, *respostas: RespostaAgente | Exception, max_reprocessamentos: int = 3
    ) -> None:
        self.leads = LeadRepositoryFake()
        self.conversas = ConversaRepositoryFake()
        self.eventos = LeadEventoRepositoryFake()
        self.agente = AgenteRoteirizado(*respostas)
        self.catalogo = CatalogoFake(item("A-1"), item("A-2"), item("A-3"))
        self.trava = TravaFake()
        self.agendador = AgendadorFake()
        self.canal = CanalFake()
        self.turno = ProcessarTurno(
            self.leads,
            self.conversas,
            self.agente,
            self.catalogo,
            eventos=self.eventos,
            persona=PERSONA,
            trava=self.trava,
            agendador=self.agendador,
            canais={Canal.WEB: self.canal},
            max_reprocessamentos=max_reprocessamentos,
        )
        self.lead = Lead.novo(Canal.WEB, "lead-1")
        self.leads.leads[self.lead.id] = self.lead
        self.conversa = Conversa.nova(self.lead)
        self.conversas.conversas[self.conversa.id] = self.conversa

    def chega(self, *textos: str) -> None:
        for texto in textos:
            self.conversas.mensagens.append(
                Mensagem.nova(self.conversa.id, Papel.LEAD, texto, status=StatusMensagem.PENDENTE)
            )

    async def processar(self):  # type: ignore[no-untyped-def]
        return await self.turno.executar(self.lead.id)


async def test_tres_mensagens_seguidas_viram_um_unico_turno() -> None:
    cenario = Cenario(RespostaAgente("Ótimo! Zona sul até 800 mil, anotado."))
    cenario.chega("procuro apê", "zona sul", "até 800 mil")

    resultado = await cenario.processar()

    assert len(cenario.agente.entradas) == 1
    assert cenario.agente.entradas[0].texto == "procuro apê\nzona sul\naté 800 mil"
    assert cenario.conversas.textos(StatusMensagem.PROCESSADA) == [
        "procuro apê",
        "zona sul",
        "até 800 mil",
    ]
    assert cenario.conversas.textos(StatusMensagem.ENVIADA) == [
        "Ótimo! Zona sul até 800 mil, anotado."
    ]
    assert [m.texto for _, m in cenario.canal.enviadas] == ["Ótimo! Zona sul até 800 mil, anotado."]
    assert resultado is not None
    assert resultado.resposta.metadados["responde_a"] == [str(m.id) for m in resultado.lote]


async def test_historico_do_agente_nao_inclui_as_pendentes_do_proprio_turno() -> None:
    cenario = Cenario(RespostaAgente("a"), RespostaAgente("b"))
    cenario.chega("oi")
    await cenario.processar()
    cenario.chega("quero comprar")

    await cenario.processar()

    historico = cenario.agente.entradas[1].historico
    assert [m.texto for m in historico] == ["oi", "a"]
    assert cenario.agente.entradas[1].texto == "quero comprar"


async def test_mensagem_durante_o_processamento_descarta_resposta_e_reprocessa() -> None:
    cenario = Cenario(
        RespostaAgente("resposta só para a 1ª"), RespostaAgente("resposta para as duas")
    )
    cenario.chega("procuro apê")
    responder_original = cenario.agente.responder

    async def responder_e_lead_digita_mais(entrada: EntradaAgente) -> RespostaAgente:
        resposta = await responder_original(entrada)
        if len(cenario.agente.entradas) == 1:
            cenario.chega("na zona sul")  # chega enquanto o agente "pensava"
        return resposta

    cenario.agente.responder = responder_e_lead_digita_mais  # type: ignore[method-assign]

    resultado = await cenario.processar()

    assert [e.texto for e in cenario.agente.entradas] == ["procuro apê", "procuro apê\nna zona sul"]
    assert cenario.conversas.textos(StatusMensagem.ENVIADA) == ["resposta para as duas"]
    assert len(cenario.canal.enviadas) == 1
    assert resultado is not None
    assert resultado.reprocessamentos == 1


async def test_teto_de_reprocessamentos_conclui_e_deixa_o_resto_para_o_proximo_turno() -> None:
    cenario = Cenario(RespostaAgente("r1"), RespostaAgente("r2"), max_reprocessamentos=1)
    cenario.chega("m1")
    responder_original = cenario.agente.responder

    async def lead_nunca_para(entrada: EntradaAgente) -> RespostaAgente:
        resposta = await responder_original(entrada)
        cenario.chega(f"extra{len(cenario.agente.entradas)}")
        return resposta

    cenario.agente.responder = lead_nunca_para  # type: ignore[method-assign]

    await cenario.processar()

    assert cenario.conversas.textos(StatusMensagem.ENVIADA) == ["r2"]
    assert cenario.conversas.textos(StatusMensagem.PROCESSADA) == ["m1", "extra1"]
    assert cenario.conversas.textos(StatusMensagem.PENDENTE) == ["extra2"]


async def test_trava_ocupada_nao_processa_e_reagenda() -> None:
    cenario = Cenario(RespostaAgente("não deveria"))
    cenario.chega("oi")
    cenario.trava.ocupadas.add(cenario.lead.id)

    resultado = await cenario.processar()

    assert resultado is None
    assert cenario.agente.entradas == []
    assert cenario.agendador.agendados == [cenario.lead.id]
    assert cenario.conversas.textos(StatusMensagem.PENDENTE) == ["oi"]


async def test_sem_pendentes_nao_chama_o_agente() -> None:
    cenario = Cenario()
    assert await cenario.processar() is None
    assert cenario.agente.entradas == []


async def test_falha_no_agente_marca_o_lote_como_falha_sem_responder() -> None:
    cenario = Cenario(LLMIndisponivelError("fora do ar"))
    cenario.chega("oi", "tudo bem?")

    assert await cenario.processar() is None

    assert cenario.conversas.textos(StatusMensagem.FALHA) == ["oi", "tudo bem?"]
    assert cenario.canal.enviadas == []


async def test_persiste_qualificacao_e_eventos_do_turno() -> None:
    cenario = Cenario()
    nova = Qualificacao(cenario.lead.id, intencao_atual="plano", fichas={"plano": {"x": 1}})
    evento = EventoLead(cenario.lead.id, TipoEvento.INTENCAO_IDENTIFICADA, cenario.lead.criado_em)
    cenario.agente = AgenteRoteirizado(
        RespostaAgente(
            "Qual unidade?",
            qualificacao=nova,
            eventos=(evento,),
            campos_faltantes=("unidade",),
            metadados={"no_resposta": "especialista"},
        )
    )
    cenario.turno._agente = cenario.agente
    cenario.chega("quero um plano")

    resultado = await cenario.processar()

    assert cenario.leads.leads[cenario.lead.id].qualificacao == nova
    assert cenario.eventos.eventos == [evento]
    assert resultado is not None
    assert resultado.campos_faltantes == ("unidade",)
    assert resultado.resposta.metadados["qualificacao"] == {
        "intencao": "plano",
        "campos_faltantes": ["unidade"],
        "score": None,
        "proxima_acao": None,
    }


# ------------------------------------------------------------------ nada fora da base


async def test_itens_sugeridos_sao_os_codigos_citados_que_existem() -> None:
    cenario = Cenario(
        RespostaAgente("Olha o A-2 e o A-1!", itens_consultados=(item("A-1"), item("A-2")))
    )
    cenario.chega("quero opções")

    resultado = await cenario.processar()

    assert resultado is not None
    assert [i.id for i in resultado.itens_sugeridos] == ["A-2", "A-1"]


async def test_codigo_citado_em_turno_anterior_e_confirmado_no_catalogo() -> None:
    cenario = Cenario(RespostaAgente("O A-3 tem varanda, sim."))
    cenario.chega("o A-3 tem varanda?")
    resultado = await cenario.processar()
    assert resultado is not None
    assert [i.id for i in resultado.itens_sugeridos] == ["A-3"]


async def test_codigo_inventado_pede_correcao_ao_agente() -> None:
    cenario = Cenario(
        RespostaAgente("Tenho o A-99!"),
        RespostaAgente("Tenho o A-1!", itens_consultados=(item("A-1"),)),
    )
    cenario.chega("opções?")

    resultado = await cenario.processar()

    assert resultado is not None
    assert resultado.resposta.texto == "Tenho o A-1!"
    instrucao = cenario.agente.entradas[1].instrucao_adicional
    assert instrucao is not None
    assert "A-99" in instrucao


async def test_insistindo_em_codigo_inventado_responde_com_fallback() -> None:
    cenario = Cenario(RespostaAgente("Tenho o A-99!"), RespostaAgente("Tenho o A-98!"))
    cenario.chega("opções?")

    resultado = await cenario.processar()

    assert resultado is not None
    assert resultado.resposta.texto == "Já te respondo!"
    assert resultado.itens_sugeridos == ()
    assert resultado.resposta.metadados["fallback"] is True
