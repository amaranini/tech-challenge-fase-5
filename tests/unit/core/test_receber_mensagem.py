import pytest

from sdr.core.application.dto.mensagem_recebida import MensagemRecebida
from sdr.core.application.use_cases.receber_mensagem import (
    MensagemInvalidaError,
    ReceberMensagem,
)
from sdr.core.domain.conversa import Canal, StatusMensagem
from sdr.core.domain.eventos import TipoEvento
from tests.apoio.fakes import (
    AgendadorFake,
    ConversaRepositoryFake,
    LeadEventoRepositoryFake,
    LeadRepositoryFake,
)


class Cenario:
    def __init__(self) -> None:
        self.leads = LeadRepositoryFake()
        self.conversas = ConversaRepositoryFake()
        self.eventos = LeadEventoRepositoryFake()
        self.agendador = AgendadorFake()
        self.caso_de_uso = ReceberMensagem(self.leads, self.conversas, self.eventos, self.agendador)

    async def enviar(self, texto: str, remetente: str = "lead-1"):  # type: ignore[no-untyped-def]
        return await self.caso_de_uso.executar(MensagemRecebida(Canal.WEB, remetente, texto))


async def test_persiste_como_pendente_e_agenda_o_turno_sem_responder() -> None:
    cenario = Cenario()

    resultado = await cenario.enviar("  procuro apê  ")

    assert resultado.mensagem.status is StatusMensagem.PENDENTE
    assert cenario.conversas.textos() == ["procuro apê"]
    assert cenario.agendador.agendados == [resultado.lead.id]


async def test_cada_mensagem_reagenda_o_mesmo_lead_na_mesma_conversa() -> None:
    cenario = Cenario()

    r1 = await cenario.enviar("procuro apê")
    r2 = await cenario.enviar("zona sul")
    r3 = await cenario.enviar("até 800 mil")

    assert r1.conversa.id == r2.conversa.id == r3.conversa.id
    assert cenario.agendador.agendados == [r1.lead.id] * 3
    assert cenario.conversas.textos(StatusMensagem.PENDENTE) == [
        "procuro apê",
        "zona sul",
        "até 800 mil",
    ]


async def test_lead_novo_emite_lead_criado_uma_unica_vez() -> None:
    cenario = Cenario()

    await cenario.enviar("oi")
    await cenario.enviar("tudo bem?")

    assert [e.tipo for e in cenario.eventos.eventos] == [TipoEvento.LEAD_CRIADO]
    assert cenario.eventos.eventos[0].payload == {"canal": "web"}


@pytest.mark.parametrize("texto", ["", "   ", "x" * 4001])
async def test_mensagem_invalida_nao_persiste_nem_agenda(texto: str) -> None:
    cenario = Cenario()
    with pytest.raises(MensagemInvalidaError):
        await cenario.enviar(texto)
    assert cenario.conversas.mensagens == []
    assert cenario.agendador.agendados == []
