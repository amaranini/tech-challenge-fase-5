"""'Digitando…' no WhatsApp enquanto a Lia pensa: no início do turno, pela última mensagem
do lote; nunca com uma pessoa da equipe atendendo; falha não derruba o turno."""

from dataclasses import replace

from sdr.core.domain.agente import RespostaAgente
from sdr.core.domain.atendimento import Atendimento, EstadoAtendimento
from sdr.core.domain.conversa import Canal, Conversa, Lead, Mensagem, Papel, StatusMensagem
from tests.unit.core.test_processar_turno import Cenario


def _no_whatsapp(cenario: Cenario, *, estado: EstadoAtendimento | None = None) -> Lead:
    lead = Lead.novo(Canal.WHATSAPP, "+5511987654321")
    if estado is not None:
        lead = replace(lead, atendimento=Atendimento(lead.id, estado, responsavel="Rafael"))
    cenario.leads.leads[lead.id] = lead
    cenario.lead = lead
    cenario.conversa = Conversa.nova(lead)
    cenario.conversas.conversas[cenario.conversa.id] = cenario.conversa
    return lead


def _chega(cenario: Cenario, *wamids: str) -> None:
    for wamid in wamids:
        cenario.conversas.mensagens.append(
            Mensagem.nova(
                cenario.conversa.id,
                Papel.LEAD,
                f"msg {wamid}",
                status=StatusMensagem.PENDENTE,
                id_externo=wamid,
            )
        )


async def test_digitando_pela_ultima_mensagem_do_lote() -> None:
    c = Cenario(RespostaAgente("Oi! Me conta o que procura?"))
    lead = _no_whatsapp(c)
    _chega(c, "wamid.1", "wamid.2", "wamid.3")  # picadas: um turno só

    resultado = await c.processar()

    assert resultado is not None
    assert c.canal.digitando == [(lead, "wamid.3")]


async def test_com_pessoa_da_equipe_atendendo_nao_indica() -> None:
    c = Cenario(RespostaAgente("", silenciar=True))
    _no_whatsapp(c, estado=EstadoAtendimento.ATENDIMENTO_HUMANO)
    _chega(c, "wamid.9")

    await c.processar()

    assert c.canal.digitando == []


async def test_falha_no_indicador_nao_derruba_o_turno() -> None:
    c = Cenario(RespostaAgente("Oi!"))
    _no_whatsapp(c)
    c.canal.falhar_digitando = True
    _chega(c, "wamid.1")

    resultado = await c.processar()

    assert resultado is not None
    assert resultado.resposta.texto == "Oi!"


async def test_chat_web_sem_id_do_provedor_nao_chama() -> None:
    c = Cenario(RespostaAgente("Oi!"))
    c.chega("oi")  # web: sem id externo
    await c.processar()
    assert c.canal.digitando == []
