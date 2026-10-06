from typing import Protocol

from sdr.domain.busca import CriteriosBusca


class InterpretadorConsultaPort(Protocol):
    """Extrai filtros estruturados de uma consulta em linguagem natural
    ("apê 2 quartos zona sul até 800 mil"). Hoje por regras; pode virar LLM."""

    def interpretar(self, texto: str) -> CriteriosBusca: ...
