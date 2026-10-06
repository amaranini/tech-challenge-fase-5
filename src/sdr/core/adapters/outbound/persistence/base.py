from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Base ORM compartilhada: tabelas do core e das verticais no mesmo metadata (Alembic)."""
