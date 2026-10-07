"""Vertical fictícia ("planos de academia") para testar o core sem a vertical imobiliária.

Schema em dicionário (sem Pydantic): prova que o core só depende do Protocol SchemaFicha.
"""

from collections.abc import Mapping

from sdr.core.domain.qualificacao import Classificacao, IntencaoVertical, Score


class SchemaFake:
    def __init__(self, tipos: Mapping[str, type]) -> None:
        self._tipos = dict(tipos)

    @property
    def campos(self) -> tuple[str, ...]:
        return tuple(self._tipos)

    def schema_extracao(self) -> Mapping[str, object]:
        nomes = {int: "integer", str: "string", list: "array"}
        return {
            "type": "object",
            "properties": {
                c: {"type": nomes[t], "description": f"descrição de {c}"}
                for c, t in self._tipos.items()
            },
        }

    def validar(self, dados: Mapping[str, object]) -> dict[str, object]:
        return {
            c: v for c, v in dados.items() if c in self._tipos and isinstance(v, self._tipos[c])
        }


class RegrasFake:
    """25 pontos por campo preenchido; qualificado com 3+ campos."""

    def pontuar(self, intencao: str, ficha: Mapping[str, object]) -> Score:
        pontos = 25 * sum(1 for v in ficha.values() if v not in (None, "", []))
        classificacao = (
            Classificacao.QUENTE
            if pontos >= 75
            else Classificacao.MORNO
            if pontos >= 50
            else Classificacao.FRIO
        )
        return Score(pontos, classificacao, (f"{pontos // 25} campo(s) preenchido(s)",))

    def qualificado(self, intencao: str, ficha: Mapping[str, object], score: Score) -> bool:
        return score.pontos >= 75


PLANO = IntencaoVertical(
    nome="plano",
    descricao="quer assinar um plano",
    schema=SchemaFake({"unidade": str, "horario": str, "orcamento": int, "modalidades": list}),
    prioridade_campos=("orcamento", "unidade", "horario"),
    prompt_especialista="PROMPT_PLANO",
    proxima_acao_ao_qualificar="agendar_aula",
)
AVULSO = IntencaoVertical(
    nome="avulso",
    descricao="quer uma diária avulsa",
    schema=SchemaFake({"unidade": str, "data": str}),
    prioridade_campos=("data", "unidade"),
    prompt_especialista="PROMPT_AVULSO",
    proxima_acao_ao_qualificar="vender_diaria",
)
INTENCOES = (PLANO, AVULSO)
