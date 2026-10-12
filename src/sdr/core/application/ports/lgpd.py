from collections.abc import Mapping
from typing import Protocol
from uuid import UUID


class DadosTitularPort(Protocol):
    """Dados pessoais de um lead, para os direitos do titular (LGPD) — ADR 015.

    `anonimizar` é atômico: ou tudo some, ou nada muda.
    """

    async def exportar(self, lead_id: UUID) -> Mapping[str, object]:
        """Tudo o que o sistema guarda sobre o lead, em estrutura serializável (JSON)."""
        ...

    async def anonimizar(self, lead_id: UUID, remetente_anonimo: str) -> Mapping[str, int]:
        """Remove o que identifica a pessoa e mantém o que serve às métricas (intenção,
        score, datas, tipos de evento). Devolve quantos registros foram tocados por tabela."""
        ...
