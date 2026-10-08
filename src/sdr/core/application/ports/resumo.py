"""Resumo de handoff: quem redige (LLM) e onde as versões ficam."""

from collections.abc import Mapping, Sequence
from typing import Protocol
from uuid import UUID

from sdr.core.domain.resumo import (
    AfirmacaoChecada,
    FatosResumo,
    RascunhoResumo,
    Resumo,
    TemplateResumo,
)


class RedatorResumoPort(Protocol):
    async def redigir(self, fatos: FatosResumo, template: TemplateResumo) -> RascunhoResumo:
        """Redige as seções do template a partir dos fatos (a ancoragem vem depois)."""
        ...

    async def checar(
        self, fatos: FatosResumo, textos: Mapping[str, str]
    ) -> Mapping[str, Sequence[AfirmacaoChecada]]:
        """Quebra cada texto livre em frases e julga cada uma contra os fatos (verificador
        independente da redação). Chave = seção."""
        ...


class ResumoRepository(Protocol):
    async def salvar(self, resumo: Resumo) -> None:
        """Insere uma nova versão (lead_id + versao são únicos)."""
        ...

    async def ultimo(self, lead_id: UUID) -> Resumo | None: ...

    async def obter(self, lead_id: UUID, versao: int) -> Resumo | None: ...

    async def versoes(self, lead_id: UUID) -> list[int]:
        """Em ordem crescente."""
        ...
