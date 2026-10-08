from datetime import UTC, datetime


class RelogioSistema:
    """RelogioPort com a hora real (UTC; cada uso converte para o fuso da operação)."""

    def agora(self) -> datetime:
        return datetime.now(UTC)
