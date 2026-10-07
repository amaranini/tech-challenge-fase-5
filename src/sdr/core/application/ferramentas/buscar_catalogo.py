"""Tool genérica de busca no catálogo da vertical (via CatalogoPort).

A vertical fornece só a DEFINIÇÃO (nome, descrição e JSON Schema dos filtros no seu
vocabulário); a execução é sempre a mesma: CatalogoPort.buscar.
"""

import json
from collections.abc import Mapping

from sdr.core.application.ports.catalogo import CatalogoPort
from sdr.core.application.ports.ferramenta import ResultadoFerramenta
from sdr.core.application.ports.llm import DefinicaoFerramenta
from sdr.core.domain.catalogo import ConsultaCatalogo, ConsultaInvalidaError

LIMITE_PADRAO = 3
LIMITE_MAXIMO = 5


class FerramentaBuscarCatalogo:
    def __init__(self, catalogo: CatalogoPort, definicao: DefinicaoFerramenta) -> None:
        self._catalogo = catalogo
        self._definicao = definicao

    @property
    def definicao(self) -> DefinicaoFerramenta:
        return self._definicao

    async def executar(self, argumentos: Mapping[str, object]) -> ResultadoFerramenta:
        texto = argumentos.get("texto")
        filtros = argumentos.get("filtros") or {}
        limite = argumentos.get("limite", LIMITE_PADRAO)
        if not isinstance(filtros, Mapping) or (texto is not None and not isinstance(texto, str)):
            return self._erro("argumentos inválidos: 'texto' deve ser texto e 'filtros' objeto")
        if not isinstance(limite, int) or isinstance(limite, bool):
            limite = LIMITE_PADRAO

        try:
            resultados = await self._catalogo.buscar(
                ConsultaCatalogo(
                    texto=texto,
                    filtros=dict(filtros),
                    limite=max(1, min(limite, LIMITE_MAXIMO)),
                )
            )
        except ConsultaInvalidaError as erro:
            return self._erro(f"filtros inválidos: {erro}")

        itens = tuple(r.item for r in resultados)
        payload = {
            "total": len(itens),
            "itens": [
                {"codigo": i.id, "titulo": i.titulo, "resumo": i.resumo, **dict(i.atributos)}
                for i in itens
            ],
        }
        if not itens:
            payload["observacao"] = "Nenhum item encontrado. Não invente opções."
        return ResultadoFerramenta(conteudo=json.dumps(payload, ensure_ascii=False), itens=itens)

    @staticmethod
    def _erro(mensagem: str) -> ResultadoFerramenta:
        conteudo = json.dumps({"erro": mensagem}, ensure_ascii=False)
        return ResultadoFerramenta(conteudo=conteudo, erro=mensagem)
