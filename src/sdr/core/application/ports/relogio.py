from datetime import datetime
from typing import Protocol


class RelogioPort(Protocol):
    """Hora atual (com fuso). Injetado para testar datas relativas com relógio fake."""

    def agora(self) -> datetime: ...
