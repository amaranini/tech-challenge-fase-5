"""Agenda da imobiliária: tipos de agendamento por intenção e responsáveis do mock."""

import json
from pathlib import Path
from uuid import UUID

from sdr.core.domain.agenda import Responsavel, TipoAgendamento
from sdr.verticals.imobiliario.qualificacao.domain.regras import ALUGUEL, COMPRA, INVESTIMENTO

VISITA_IMOVEL = TipoAgendamento(
    nome="visita_imovel",
    rotulo="visita aos imóveis com um corretor",
    modalidades={"presencial": "presencial, no imóvel"},
)
REUNIAO_ESPECIALISTA = TipoAgendamento(
    nome="reuniao_especialista",
    rotulo="reunião com o especialista em investimentos",
    modalidades={
        "online": "online, por videochamada",
        "escritorio": "presencial, no escritório da imobiliária",
    },
)
TIPOS_AGENDAMENTO: dict[str, TipoAgendamento] = {
    COMPRA: VISITA_IMOVEL,
    ALUGUEL: VISITA_IMOVEL,
    INVESTIMENTO: REUNIAO_ESPECIALISTA,
}


def ler_responsaveis(caminho: Path) -> list[Responsavel]:
    return [
        Responsavel(
            id=UUID(r["id"]),
            nome=r["nome"],
            titulo=r["titulo"],
            especialidades=tuple(r["especialidades"]),
        )
        for r in json.loads(caminho.read_text(encoding="utf-8"))
    ]
