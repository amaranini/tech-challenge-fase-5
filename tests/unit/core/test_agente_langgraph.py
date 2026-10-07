from sdr.core.adapters.outbound.agent.agente_langgraph import AgenteLangGraph
from sdr.core.application.ferramentas.buscar_catalogo import FerramentaBuscarCatalogo
from sdr.core.application.ports.agente import EntradaAgente
from sdr.core.application.ports.llm import DefinicaoFerramenta, PapelLLM
from sdr.core.domain.agente import Persona
from sdr.core.domain.conversa import Canal, Conversa, Lead, Mensagem, Papel
from tests.apoio.fakes import CatalogoFake, LLMRoteirizado, chamar, item, texto

PERSONA = Persona(nome="Ana", versao_prompt="v1", prompt_sistema="Você é a Ana.")
DEFINICAO = DefinicaoFerramenta("buscar", "Busca no catálogo", {"type": "object"})
LEAD = Lead.novo(Canal.WEB, "lead-1")
CONVERSA = Conversa.nova(LEAD)


def agente(llm: LLMRoteirizado, catalogo: CatalogoFake | None = None, **kw: int) -> AgenteLangGraph:
    ferramenta = FerramentaBuscarCatalogo(catalogo or CatalogoFake(), DEFINICAO)
    return AgenteLangGraph(llm, PERSONA, [ferramenta], **kw)


def entrada(textual: str = "oi", historico: list[Mensagem] | None = None) -> EntradaAgente:
    return EntradaAgente(lead=LEAD, historico=historico or [], texto=textual)


async def test_responde_direto_sem_ferramenta() -> None:
    llm = LLMRoteirizado(texto("Oi! Tudo bem?"))

    resposta = await agente(llm).responder(entrada())

    assert resposta.texto == "Oi! Tudo bem?"
    assert resposta.itens_consultados == ()
    assert resposta.modelo == "fake-1"
    assert len(llm.chamadas) == 1


async def test_executa_ferramenta_e_volta_ao_llm_com_o_resultado() -> None:
    catalogo = CatalogoFake(item("A-1"), item("A-2"))
    llm = LLMRoteirizado(chamar("buscar", texto="apto"), texto("Achei o A-1 e o A-2."))

    resposta = await agente(llm, catalogo).responder(entrada("quero um apto"))

    assert [i.id for i in resposta.itens_consultados] == ["A-1", "A-2"]
    assert resposta.chamadas[0].nome == "buscar"
    assert resposta.tokens_entrada == 5 + 10
    segunda_chamada = llm.chamadas[1][0]
    assert segunda_chamada[-1].papel is PapelLLM.FERRAMENTA
    assert '"codigo": "A-1"' in segunda_chamada[-1].conteudo


async def test_ferramenta_inexistente_vira_erro_para_o_llm() -> None:
    llm = LLMRoteirizado(chamar("voar"), texto("Desculpe!"))

    resposta = await agente(llm).responder(entrada())

    assert resposta.chamadas[0].erro == "ferramenta inexistente"
    assert "não existe" in llm.chamadas[1][0][-1].conteudo


async def test_esgotados_os_passos_forca_resposta_em_texto() -> None:
    llm = LLMRoteirizado(chamar("buscar", "c1"), chamar("buscar", "c2"), texto("Pronto."))

    resposta = await agente(llm, max_passos=2).responder(entrada())

    assert resposta.texto == "Pronto."
    assert [forcar for _, _, forcar in llm.chamadas] == [False, False, True]


async def test_monta_contexto_com_persona_historico_e_itens_ja_apresentados() -> None:
    historico = [
        Mensagem.nova(CONVERSA.id, Papel.LEAD, "procuro apto"),
        Mensagem.nova(
            CONVERSA.id,
            Papel.AGENTE,
            "Tenho o A-1!",
            metadados={"itens_citados": [{"id": "A-1", "resumo": "2 quartos"}]},
        ),
    ]
    llm = LLMRoteirizado(texto("Claro!"))

    await agente(llm).responder(entrada("gostei do primeiro", historico))

    mensagens = llm.chamadas[0][0]
    papeis = [m.papel for m in mensagens]
    assert mensagens[0].conteudo == "Você é a Ana."
    assert papeis == [
        PapelLLM.SISTEMA,  # persona
        PapelLLM.SISTEMA,  # data/hora
        PapelLLM.USUARIO,
        PapelLLM.ASSISTENTE,
        PapelLLM.SISTEMA,  # lembrete dos itens apresentados
        PapelLLM.USUARIO,
    ]
    assert "A-1: 2 quartos" in mensagens[4].conteudo
    assert mensagens[-1].conteudo == "gostei do primeiro"


async def test_resposta_vazia_do_llm_usa_fallback_da_persona() -> None:
    resposta = await agente(LLMRoteirizado(texto("   "))).responder(entrada())
    assert resposta.texto == PERSONA.mensagem_fallback
