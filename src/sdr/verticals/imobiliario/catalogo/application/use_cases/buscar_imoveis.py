from dataclasses import dataclass, field

from sdr.core.application.ports.embedding import EmbeddingPort
from sdr.verticals.imobiliario.catalogo.application.ports import (
    IndiceImoveisPort,
    InterpretadorConsultaPort,
)
from sdr.verticals.imobiliario.catalogo.domain.criterios import CriteriosBusca, ImovelEncontrado

LIMITE_MAXIMO = 20


@dataclass(frozen=True)
class ConsultaImoveis:
    texto: str | None = None
    criterios: CriteriosBusca = field(default_factory=CriteriosBusca)
    limite: int = 5
    interpretar_texto: bool = True


@dataclass(frozen=True)
class ResultadoBusca:
    criterios_aplicados: CriteriosBusca
    imoveis: list[ImovelEncontrado]


class BuscarImoveis:
    """Busca híbrida: filtros (explícitos + inferidos do texto) e similaridade semântica."""

    def __init__(
        self,
        busca: IndiceImoveisPort,
        embedding: EmbeddingPort,
        interpretador: InterpretadorConsultaPort,
    ) -> None:
        self._busca = busca
        self._embedding = embedding
        self._interpretador = interpretador

    async def executar(self, consulta: ConsultaImoveis) -> ResultadoBusca:
        texto = (consulta.texto or "").strip()
        limite = max(1, min(consulta.limite, LIMITE_MAXIMO))

        criterios = consulta.criterios
        if texto and consulta.interpretar_texto:
            criterios = self._interpretador.interpretar(texto).sobrescrever_com(criterios)

        vetor = await self._embedding.gerar_consulta(texto) if texto else None
        imoveis = await self._busca.buscar(criterios, vetor, limite)
        return ResultadoBusca(criterios_aplicados=criterios, imoveis=imoveis)
