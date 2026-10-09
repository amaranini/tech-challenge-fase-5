"""VerticalPack imobiliário: composition root da vertical, chamado pelo bootstrap."""

from pathlib import Path

from sdr.core.application.ferramentas.buscar_catalogo import FerramentaBuscarCatalogo
from sdr.core.vertical import InfraCompartilhada, VerticalMontada
from sdr.verticals.imobiliario.agenda.adapters.agenda_imobiliaria import (
    TIPOS_AGENDAMENTO,
    ler_responsaveis,
)
from sdr.verticals.imobiliario.agenda.domain.atribuicao import RegraAtribuicaoImobiliaria
from sdr.verticals.imobiliario.catalogo.adapters.carga_json import ler_imoveis_json
from sdr.verticals.imobiliario.catalogo.adapters.catalogo_imobiliario import CatalogoImobiliario
from sdr.verticals.imobiliario.catalogo.adapters.ferramenta_buscar_imoveis import (
    DEFINICAO_BUSCAR_IMOVEIS,
)
from sdr.verticals.imobiliario.catalogo.adapters.http import criar_router
from sdr.verticals.imobiliario.catalogo.adapters.indice_pgvector import IndiceImoveisPgvector
from sdr.verticals.imobiliario.catalogo.adapters.interpretador_regras import InterpretadorRegras
from sdr.verticals.imobiliario.catalogo.adapters.persistence.modelos import DIMENSAO_EMBEDDING
from sdr.verticals.imobiliario.catalogo.adapters.persistence.repositorio_sql import (
    ImovelRepositorySql,
)
from sdr.verticals.imobiliario.catalogo.application.use_cases.buscar_imoveis import BuscarImoveis
from sdr.verticals.imobiliario.catalogo.application.use_cases.cadastrar_imoveis import (
    CadastrarImoveis,
)
from sdr.verticals.imobiliario.config import SettingsImobiliario
from sdr.verticals.imobiliario.followup.cadencia import (
    CADENCIAS,
    LEMBRETE_AGENDAMENTO,
    ConsultaFollowUpImobiliaria,
)
from sdr.verticals.imobiliario.followup.templates import RESPOSTA_EQUIPE
from sdr.verticals.imobiliario.persona.lia import carregar_persona, carregar_prompt
from sdr.verticals.imobiliario.qualificacao.adapters.intencoes import definir_intencoes
from sdr.verticals.imobiliario.qualificacao.domain.regras import RegrasImobiliarias
from sdr.verticals.imobiliario.resumo.template import TEMPLATE_RESUMO

DADOS = Path(__file__).resolve().parent / "dados"
CATALOGO_INICIAL = DADOS / "imoveis.json"
RESPONSAVEIS = DADOS / "responsaveis.json"


class PackImobiliario:
    def __init__(
        self,
        settings: SettingsImobiliario | None = None,
        catalogo_inicial: Path = CATALOGO_INICIAL,
    ) -> None:
        self._settings = settings or SettingsImobiliario()
        self._catalogo_inicial = catalogo_inicial

    @property
    def nome(self) -> str:
        return "imobiliario"

    def montar(self, infra: InfraCompartilhada) -> VerticalMontada:
        if infra.embedding.dimensao != DIMENSAO_EMBEDDING:
            raise RuntimeError(
                f"Embedding com dimensão {infra.embedding.dimensao}, mas a coluna vetorial de "
                f"imóveis tem {DIMENSAO_EMBEDDING}: crie uma migration e reindexe antes."
            )

        repositorio = ImovelRepositorySql(infra.sessoes)
        indice = IndiceImoveisPgvector(infra.sessoes)
        interpretador = InterpretadorRegras(self._settings.distancia_metro_padrao_m)
        buscar_imoveis = BuscarImoveis(indice, infra.embedding, interpretador)
        cadastrar_imoveis = CadastrarImoveis(repositorio, indice, infra.embedding)

        async def carregar_catalogo_inicial() -> int:
            return await cadastrar_imoveis.executar(ler_imoveis_json(self._catalogo_inicial))

        catalogo = CatalogoImobiliario(buscar_imoveis, repositorio)
        zona_por_bairro = {i.bairro: i.zona.value for i in ler_imoveis_json(self._catalogo_inicial)}
        regra = RegraAtribuicaoImobiliaria(zona_por_bairro)
        return VerticalMontada(
            catalogo=catalogo,
            carregar_catalogo_inicial=carregar_catalogo_inicial,
            persona=carregar_persona(self._settings.versao_prompt),
            intencoes=definir_intencoes(
                {
                    nome: carregar_prompt(f"especialistas/{nome}_v1")
                    for nome in ("compra", "aluguel", "investimento")
                }
            ),
            regras_qualificacao=RegrasImobiliarias(),
            prompt_descoberta=carregar_prompt("descoberta_v2"),
            ferramentas=[FerramentaBuscarCatalogo(catalogo, DEFINICAO_BUSCAR_IMOVEIS)],
            routers=[criar_router(buscar_imoveis, repositorio)],
            regra_atribuicao=regra,
            tipos_agendamento=TIPOS_AGENDAMENTO,
            responsaveis_iniciais=ler_responsaveis(RESPONSAVEIS),
            template_resumo=TEMPLATE_RESUMO,
            cadencias_followup=CADENCIAS,
            consulta_followup=ConsultaFollowUpImobiliaria(regra),
            lembrete_agendamento=LEMBRETE_AGENDAMENTO,
            template_resposta_responsavel=RESPOSTA_EQUIPE,
        )
