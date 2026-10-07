"""Status das mensagens (pendente/processada/falha/enviada) para turnos assíncronos.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Mensagens existentes já foram respondidas no fluxo síncrono: entram como processadas.
    op.add_column(
        "mensagens",
        sa.Column("status", sa.String(20), nullable=False, server_default="processada"),
    )
    op.execute("UPDATE mensagens SET status = 'enviada' WHERE papel = 'agente'")
    op.create_index("ix_mensagens_conversa_status", "mensagens", ["conversa_id", "status"])


def downgrade() -> None:
    op.drop_index("ix_mensagens_conversa_status", table_name="mensagens")
    op.drop_column("mensagens", "status")
