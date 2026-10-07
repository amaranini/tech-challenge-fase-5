"""AgendadorDebounce com relógio fake: o tempo só anda quando o teste manda."""

import asyncio
from uuid import UUID, uuid4

import pytest

from sdr.core.adapters.outbound.turnos.agendador_debounce import AgendadorDebounce


class RelogioFake:
    def __init__(self) -> None:
        self.agora_s = 0.0
        self._dormindo: list[tuple[float, asyncio.Future[None]]] = []

    def agora(self) -> float:
        return self.agora_s

    async def dormir(self, segundos: float) -> None:
        futuro: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        registro = (self.agora_s + segundos, futuro)
        self._dormindo.append(registro)
        try:
            await futuro
        finally:
            self._dormindo.remove(registro)

    async def avancar(self, segundos: float) -> None:
        alvo = self.agora_s + segundos
        while True:
            await _girar()
            prontos = sorted(
                (r for r in self._dormindo if r[0] <= alvo and not r[1].done()),
                key=lambda r: r[0],
            )
            if not prontos:
                break
            prazo, futuro = prontos[0]
            self.agora_s = prazo
            futuro.set_result(None)
        self.agora_s = alvo
        await _girar()


async def _girar() -> None:
    for _ in range(10):
        await asyncio.sleep(0)


class Executor:
    def __init__(self, relogio: RelogioFake, duracao: float = 0.0) -> None:
        self.relogio = relogio
        self.duracao = duracao
        self.execucoes: list[tuple[UUID, float]] = []
        self.em_andamento = 0
        self.maximo_simultaneo = 0

    async def __call__(self, lead_id: UUID) -> None:
        self.execucoes.append((lead_id, self.relogio.agora()))
        self.em_andamento += 1
        self.maximo_simultaneo = max(self.maximo_simultaneo, self.em_andamento)
        try:
            if self.duracao:
                await self.relogio.dormir(self.duracao)
        finally:
            self.em_andamento -= 1


def montar(janela: float = 5, teto: float = 20, duracao: float = 0.0):  # type: ignore[no-untyped-def]
    relogio = RelogioFake()
    executor = Executor(relogio, duracao)
    return AgendadorDebounce(janela, teto, executor, relogio), relogio, executor


LEAD = uuid4()


async def test_tres_mensagens_em_sequencia_rapida_geram_um_unico_turno() -> None:
    agendador, relogio, executor = montar()

    agendador.agendar(LEAD)  # t=0 "procuro apê"
    await relogio.avancar(1)
    agendador.agendar(LEAD)  # t=1 "zona sul"
    await relogio.avancar(2)
    agendador.agendar(LEAD)  # t=3 "até 800 mil"
    await relogio.avancar(4.9)
    assert executor.execucoes == []  # ainda dentro da janela de silêncio

    await relogio.avancar(0.2)

    assert executor.execucoes == [(LEAD, 8.0)]  # 5s após a última mensagem
    await relogio.avancar(60)
    assert len(executor.execucoes) == 1


async def test_teto_maximo_e_respeitado_mesmo_com_mensagens_sem_parar() -> None:
    agendador, relogio, executor = montar(janela=5, teto=12)

    for _ in range(10):  # uma mensagem a cada 4s: a janela de 5s nunca fecharia
        agendador.agendar(LEAD)
        await relogio.avancar(4)

    assert executor.execucoes[0] == (LEAD, 12.0)  # teto contado da 1ª mensagem


async def test_leads_diferentes_tem_ciclos_independentes() -> None:
    agendador, relogio, executor = montar()
    outro = uuid4()

    agendador.agendar(LEAD)
    await relogio.avancar(3)
    agendador.agendar(outro)
    await relogio.avancar(10)

    assert executor.execucoes == [(LEAD, 5.0), (outro, 8.0)]


async def test_mensagem_durante_o_turno_abre_novo_ciclo_sem_cancelar_o_atual() -> None:
    agendador, relogio, executor = montar(janela=5, teto=20, duracao=10)

    agendador.agendar(LEAD)
    await relogio.avancar(6)  # turno começou em t=5 e dura até t=15
    assert executor.em_andamento == 1

    agendador.agendar(LEAD)  # t=6, durante o processamento
    await relogio.avancar(30)

    assert [t for _, t in executor.execucoes] == [5.0, 11.0]  # novo ciclo: 5s após t=6
    # A sobreposição em memória é esperada: quem serializa é a TravaTurnoPort do ProcessarTurno.
    assert executor.maximo_simultaneo == 2


async def test_erro_no_turno_nao_derruba_o_agendador() -> None:
    relogio = RelogioFake()
    chamadas: list[UUID] = []

    async def explode(lead_id: UUID) -> None:
        chamadas.append(lead_id)
        raise RuntimeError("boom")

    agendador = AgendadorDebounce(1, 5, explode, relogio)
    agendador.agendar(LEAD)
    await relogio.avancar(2)
    agendador.agendar(LEAD)
    await relogio.avancar(2)

    assert chamadas == [LEAD, LEAD]


async def test_encerrar_cancela_turnos_aguardando() -> None:
    agendador, relogio, executor = montar()
    agendador.agendar(LEAD)
    assert agendador.aguardando == {LEAD}

    await agendador.encerrar()
    await relogio.avancar(60)

    assert executor.execucoes == []


def test_parametros_invalidos() -> None:
    with pytest.raises(ValueError, match="janela_s <= teto_s"):
        AgendadorDebounce(10, 5)
