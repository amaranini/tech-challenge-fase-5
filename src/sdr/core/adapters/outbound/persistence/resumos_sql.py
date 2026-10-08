"""Versões do resumo de handoff em Postgres (resumos_handoff)."""

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sdr.core.adapters.outbound.persistence.modelos import ResumoHandoffModel
from sdr.core.domain.resumo import Resumo, SecaoPreenchida, TipoSecao


def secoes_para_json(resumo: Resumo) -> list[dict[str, Any]]:
    return [
        {"chave": s.chave, "titulo": s.titulo, "tipo": s.tipo.value, "conteudo": s.conteudo}
        for s in resumo.secoes
    ]


def _resumo(m: ResumoHandoffModel) -> Resumo:
    return Resumo(
        id=m.id,
        lead_id=m.lead_id,
        versao=m.versao,
        gerado_em=m.gerado_em,
        gatilho=m.gatilho,
        template_versao=m.template_versao,
        titulo=m.titulo,
        secoes=tuple(
            SecaoPreenchida(s["chave"], s["titulo"], TipoSecao(s["tipo"]), s["conteudo"])
            for s in m.secoes
        ),
        impressao=m.impressao,
        descartados=tuple(m.descartados or ()),
        modelo=m.modelo,
        tokens_entrada=m.tokens_entrada,
        tokens_saida=m.tokens_saida,
    )


class ResumoRepositorySql:
    def __init__(self, sessoes: async_sessionmaker[AsyncSession]) -> None:
        self._sessoes = sessoes

    async def salvar(self, resumo: Resumo) -> None:
        async with self._sessoes.begin() as sessao:
            sessao.add(
                ResumoHandoffModel(
                    id=resumo.id,
                    lead_id=resumo.lead_id,
                    versao=resumo.versao,
                    gerado_em=resumo.gerado_em,
                    gatilho=resumo.gatilho,
                    template_versao=resumo.template_versao,
                    titulo=resumo.titulo,
                    secoes=secoes_para_json(resumo),
                    impressao=resumo.impressao,
                    descartados=list(resumo.descartados),
                    modelo=resumo.modelo,
                    tokens_entrada=resumo.tokens_entrada,
                    tokens_saida=resumo.tokens_saida,
                )
            )

    async def ultimo(self, lead_id: UUID) -> Resumo | None:
        async with self._sessoes() as sessao:
            modelo = await sessao.scalar(
                select(ResumoHandoffModel)
                .where(ResumoHandoffModel.lead_id == lead_id)
                .order_by(ResumoHandoffModel.versao.desc())
                .limit(1)
            )
        return _resumo(modelo) if modelo else None

    async def obter(self, lead_id: UUID, versao: int) -> Resumo | None:
        async with self._sessoes() as sessao:
            modelo = await sessao.scalar(
                select(ResumoHandoffModel).where(
                    ResumoHandoffModel.lead_id == lead_id, ResumoHandoffModel.versao == versao
                )
            )
        return _resumo(modelo) if modelo else None

    async def versoes(self, lead_id: UUID) -> list[int]:
        async with self._sessoes() as sessao:
            versoes = await sessao.scalars(
                select(ResumoHandoffModel.versao)
                .where(ResumoHandoffModel.lead_id == lead_id)
                .order_by(ResumoHandoffModel.versao)
            )
            return list(versoes)
