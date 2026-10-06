from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum


class Finalidade(StrEnum):
    VENDA = "venda"
    ALUGUEL = "aluguel"


class Zona(StrEnum):
    SUL = "sul"
    OESTE = "oeste"
    NORTE = "norte"
    LESTE = "leste"
    CENTRO = "centro"


class TipoImovel(StrEnum):
    APARTAMENTO = "apartamento"
    STUDIO = "studio"
    COBERTURA = "cobertura"
    CASA = "casa"
    SOBRADO = "sobrado"


class ImovelInvalidoError(ValueError):
    pass


@dataclass(frozen=True)
class Imovel:
    """Imóvel da carteira. `preco` é o valor total na venda ou o aluguel mensal na locação."""

    id: str
    titulo: str
    tipo: TipoImovel
    finalidade: Finalidade
    zona: Zona
    bairro: str
    preco: Decimal
    condominio: Decimal
    iptu_mensal: Decimal
    quartos: int
    suites: int
    vagas: int
    area_m2: Decimal
    descricao: str
    estacao_metro: str | None = None
    distancia_metro_m: int | None = None
    comodidades: tuple[str, ...] = field(default_factory=tuple)
    aceita_pet: bool = False
    mobiliado: bool = False
    rentabilidade_estimada_aa: Decimal | None = None

    def __post_init__(self) -> None:
        erros: list[str] = []
        if self.preco <= 0:
            erros.append("preço deve ser positivo")
        if self.condominio < 0 or self.iptu_mensal < 0:
            erros.append("condomínio e IPTU não podem ser negativos")
        if min(self.quartos, self.suites, self.vagas) < 0:
            erros.append("quartos, suítes e vagas não podem ser negativos")
        if self.suites > self.quartos:
            erros.append("suítes não podem exceder quartos")
        if self.area_m2 <= 0:
            erros.append("área deve ser positiva")
        if (self.estacao_metro is None) != (self.distancia_metro_m is None):
            erros.append("estação e distância do metrô devem vir juntas")
        if self.distancia_metro_m is not None and self.distancia_metro_m < 0:
            erros.append("distância do metrô não pode ser negativa")
        if self.rentabilidade_estimada_aa is not None and self.finalidade is Finalidade.ALUGUEL:
            erros.append("rentabilidade estimada só se aplica a imóveis à venda")
        if erros:
            raise ImovelInvalidoError(f"Imóvel {self.id}: " + "; ".join(erros))

    @property
    def custo_mensal(self) -> Decimal:
        """Aluguel + condomínio + IPTU (locação) ou condomínio + IPTU (venda)."""
        base = self.preco if self.finalidade is Finalidade.ALUGUEL else Decimal(0)
        return base + self.condominio + self.iptu_mensal

    def texto_semantico(self) -> str:
        """Texto que representa o imóvel na busca semântica."""
        partes = [
            self.titulo,
            f"{self.tipo} para {self.finalidade} em {self.bairro}, zona {self.zona} de São Paulo.",
            self.descricao,
        ]
        if self.estacao_metro:
            partes.append(f"Próximo à estação {self.estacao_metro} do metrô.")
        if self.comodidades:
            partes.append("Comodidades: " + ", ".join(self.comodidades) + ".")
        if self.aceita_pet:
            partes.append("Aceita animais de estimação.")
        if self.mobiliado:
            partes.append("Mobiliado.")
        return " ".join(partes)
