from typing import Protocol


class LimitadorTaxaPort(Protocol):
    """Rate limit por chave (ex.: "ip:1.2.3.4", "remetente:web:lead-ana") — ADR 015.

    POC: em memória, por processo (a API tem 1 réplica — ADR 006). Com várias réplicas,
    troca-se o adapter (Redis/Postgres) sem mudar quem chama.
    """

    async def consumir(self, chave: str, limite_por_minuto: int) -> float:
        """Consome uma unidade. 0 = permitido; > 0 = segundos até a próxima liberação."""
        ...
