"""Follow-up da imobiliária: cadência por situação do lead e a busca de "algo novo".

Puro: só declara as etapas (D+1, D+3, encerramento em D+7 — em minutos na demo, via
FOLLOWUP_UNIDADE) e traduz a ficha para os filtros do catálogo. Quem agenda, confere a
elegibilidade e envia é o core.
"""

from collections.abc import Mapping

from sdr.core.domain.catalogo import ConsultaCatalogo
from sdr.core.domain.followup import Cadencia, EtapaCadencia, ModeloLembrete, SituacaoLead
from sdr.verticals.imobiliario.agenda.domain.atribuicao import RegraAtribuicaoImobiliaria
from sdr.verticals.imobiliario.qualificacao.domain.regras import ALUGUEL, COMPRA, INVESTIMENTO

# Templates aprovados no WhatsApp (usados só fora da janela de 24h): nomes estáveis aqui; o
# conteúdo aprovado na Meta e as variáveis entram com o canal WhatsApp (Dia 4).
ENCERRAMENTO = EtapaCadencia(
    7 - 3,
    "Encerrar com educação: dizer que não vai mais incomodar, que entende se agora não é o "
    "momento e que é só chamar quando quiser retomar a busca.",
    template="imob_encerramento_busca",
    encerramento=True,
)

CADENCIAS: dict[SituacaoLead, Cadencia] = {
    SituacaoLead.DESCOBERTA: Cadencia(
        (
            EtapaCadencia(
                1,
                "Retomar com leveza: perguntar se ainda está procurando imóvel e se é para "
                "comprar, alugar ou investir.",
                template="imob_retomada_descoberta",
            ),
            EtapaCadencia(
                2,
                "Oferecer ajuda de novo, com uma pergunta simples sobre a região.",
                template="imob_retomada_regiao",
            ),
            ENCERRAMENTO,
        )
    ),
    SituacaoLead.QUALIFICACAO: Cadencia(
        (
            EtapaCadencia(
                1,
                "Retomar do ponto onde a conversa parou, lembrando o que a pessoa já contou, "
                "e trazer o imóvel novo compatível (se houver); seguir com a próxima dúvida "
                "da busca.",
                template="imob_retomada_busca",
            ),
            EtapaCadencia(
                2,
                "Trazer uma novidade útil (imóvel novo compatível, se houver) e perguntar se "
                "quer ajustar algum critério.",
                template="imob_novidade_imovel",
            ),
            ENCERRAMENTO,
        )
    ),
    SituacaoLead.QUALIFICADO: Cadencia(
        (
            EtapaCadencia(
                1,
                "Retomar lembrando o que a pessoa procura e convidar para o próximo passo "
                "(conhecer os imóveis com um corretor ou conversar com o especialista).",
                template="imob_convite_visita",
            ),
            EtapaCadencia(
                2,
                "Trazer o imóvel novo compatível (se houver) e perguntar se faz sentido "
                "marcar um horário.",
                template="imob_novidade_imovel",
            ),
            ENCERRAMENTO,
        )
    ),
}

LEMBRETE_AGENDAMENTO = ModeloLembrete(
    objetivo=(
        "Lembrar da visita ou da reunião marcada (o que é, quando, com quem e se é no imóvel, "
        "online ou no escritório — como está nos dados) e perguntar se continua de pé; se "
        "não puder, oferecer remarcar."
    ),
    template="imob_lembrete_agendamento",
)


class ConsultaFollowUpImobiliaria:
    """Ficha → busca no catálogo (mesmo vocabulário de filtros da tool `buscar_imoveis`)."""

    def __init__(self, regra: RegraAtribuicaoImobiliaria) -> None:
        self._regra = regra  # reaproveita a leitura de zona/bairro da região

    def __call__(self, intencao: str, ficha: Mapping[str, object]) -> ConsultaCatalogo | None:
        filtros: dict[str, object] = {}
        zona = self._regra.zona(ficha.get("regiao"))
        if zona:
            filtros["zonas"] = [zona]
        if isinstance(ficha.get("quartos"), int):
            filtros["quartos_min"] = ficha["quartos"]
        if intencao == COMPRA:
            filtros["finalidade"] = "venda"
            teto = ficha.get("preco_max")
        elif intencao == ALUGUEL:
            filtros["finalidade"] = "aluguel"
            teto = ficha.get("aluguel_max")
        elif intencao == INVESTIMENTO:
            filtros["finalidade"] = "venda"
            teto = ficha.get("ticket")
        else:
            return None
        if isinstance(teto, int | float):
            filtros["preco_max"] = teto
        texto = " ".join(
            str(v) for k, v in ficha.items() if k in ("regiao", "objetivo", "tipo_imovel") and v
        )
        return ConsultaCatalogo(texto=texto or None, filtros=filtros, limite=5)
