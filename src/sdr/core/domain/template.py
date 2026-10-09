"""Templates de mensagem ativa: o que o canal exige fora da janela de conversa.

No WhatsApp, passadas 24h da última mensagem do lead, a empresa só pode escrever com um
template aprovado pela Meta. O core trata isso de forma genérica:

- a VERTICAL declara cada `TemplateLogico` (nome lógico, texto de referência para
  aprovação, variáveis nomeadas e a regra de preenchimento de cada uma);
- a OPERAÇÃO mapeia o nome lógico para o template aprovado no provedor (config);
- o CORE preenche as variáveis a partir do contexto, passa TODAS pelo validador e cai no
  valor padrão quando faltar dado ou o valor for recusado. O LLM só escreve as variáveis
  marcadas como "gancho" — e também passa pelo validador.
"""

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from zoneinfo import ZoneInfo

from sdr.core.domain.agenda import Agendamento
from sdr.core.domain.catalogo import ItemCatalogo
from sdr.core.domain.conversa import Lead
from sdr.core.domain.qualificacao import Ficha

PLACEHOLDER = re.compile(r"\{\{\s*([a-z][a-z0-9_]*)\s*\}\}")
NOME_TEMPLATE = re.compile(r"^[a-z][a-z0-9_]{0,511}$")  # regra de nome da Meta
MAX_CARACTERES_VARIAVEL = 200


class CategoriaTemplate(StrEnum):
    """Categoria exigida pela Meta na submissão."""

    UTILITY = "UTILITY"  # transacional: lembrete, confirmação, retomada de atendimento
    MARKETING = "MARKETING"  # promocional/reengajamento


@dataclass(frozen=True)
class ContextoTemplate:
    """O que a regra de preenchimento de uma variável pode ler."""

    lead: Lead
    fuso: ZoneInfo
    agendamento: Agendamento | None = None
    itens_novos: tuple[ItemCatalogo, ...] = ()
    responsavel: str | None = None  # pessoa da equipe que escreve (atendimento humano)
    ganchos: Mapping[str, str] = field(default_factory=dict)  # escritos pelo LLM

    @property
    def primeiro_nome(self) -> str | None:
        nome = (self.lead.nome or "").strip()
        return nome.split()[0] if nome else None

    @property
    def intencao(self) -> str | None:
        return self.lead.qualificacao.intencao_atual

    @property
    def ficha(self) -> Ficha:
        return self.lead.qualificacao.ficha


Preenchedor = Callable[[ContextoTemplate], str | None]


@dataclass(frozen=True)
class VariavelTemplate:
    nome: str
    descricao: str  # regra de preenchimento (documentação e, se gancho, instrução ao LLM)
    padrao: str  # fallback: falta de dado ou valor recusado pelo validador
    preencher: Preenchedor | None = None  # da ficha/contexto (puro)
    gancho: bool = False  # escrita pelo LLM (personalização curta)
    max_caracteres: int | None = None  # além do limite global da operação
    exemplo: str | None = None  # valor de amostra exigido na submissão (padrão: `padrao`)

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9_]*", self.nome):
            raise ValueError(f"variável {self.nome!r}: use minúsculas, dígitos e _")
        if self.gancho and self.preencher is not None:
            raise ValueError(f"variável {self.nome!r}: gancho é escrito pelo LLM, sem regra")
        if validar_variavel(self.padrao, self.max_caracteres or MAX_CARACTERES_VARIAVEL) is None:
            raise ValueError(f"variável {self.nome!r}: valor padrão inválido")


@dataclass(frozen=True)
class TemplateLogico:
    nome: str
    categoria: CategoriaTemplate
    texto_referencia: str  # com {{nome_da_variavel}}; base da submissão à Meta
    variaveis: tuple[VariavelTemplate, ...]
    idioma: str = "pt_BR"

    def __post_init__(self) -> None:
        if not NOME_TEMPLATE.match(self.nome):
            raise ValueError(f"template {self.nome!r}: nome fora do padrão da Meta")
        nomes = [v.nome for v in self.variaveis]
        if nomes != list(self.ordem):
            raise ValueError(
                f"template {self.nome!r}: variáveis {nomes} ≠ ordem no texto {list(self.ordem)}"
            )
        texto = self.texto_referencia.strip()
        # Regras de aprovação da Meta: variável não pode abrir/fechar o texto nem vir colada.
        if texto.startswith("{{") or texto.endswith("}}"):
            raise ValueError(f"template {self.nome!r}: texto não pode começar/terminar em variável")
        if re.search(r"\}\}\s*\{\{", texto):
            raise ValueError(f"template {self.nome!r}: variáveis adjacentes")

    @property
    def ordem(self) -> tuple[str, ...]:
        """Nomes na ordem da 1ª aparição no texto = numeração {{1}}, {{2}}… no provedor."""
        vistos: dict[str, None] = {}
        for nome in PLACEHOLDER.findall(self.texto_referencia):
            vistos.setdefault(nome, None)
        return tuple(vistos)

    @property
    def ganchos(self) -> tuple[VariavelTemplate, ...]:
        return tuple(v for v in self.variaveis if v.gancho)

    def numerado(self) -> str:
        """Texto no formato do provedor ({{1}}, {{2}}…), para a submissão."""
        posicao = {nome: i for i, nome in enumerate(self.ordem, start=1)}
        return PLACEHOLDER.sub(
            lambda m: "{{" + str(posicao[m.group(1)]) + "}}", self.texto_referencia
        )

    def renderizar(self, valores: Mapping[str, str]) -> str:
        return PLACEHOLDER.sub(lambda m: valores[m.group(1)], self.texto_referencia)


def validar_variavel(valor: str | None, max_caracteres: int) -> str | None:
    """Valor aceito pelo provedor, ou None: sem quebra de linha/tab, sem 4+ espaços
    seguidos, não vazio e dentro do tamanho máximo."""
    if valor is None:
        return None
    texto = valor.strip()
    if not texto or len(texto) > max_caracteres:
        return None
    if any(c in texto for c in "\n\r\t") or "    " in texto:
        return None
    return texto


@dataclass(frozen=True)
class TemplatePreenchido:
    template: TemplateLogico
    valores: dict[str, str]  # nomeados, na ordem do template
    fallbacks: tuple[str, ...] = ()  # variáveis que caíram no valor padrão

    @property
    def texto(self) -> str:
        return self.template.renderizar(self.valores)


def preencher_template(
    template: TemplateLogico,
    contexto: ContextoTemplate,
    max_caracteres: int = MAX_CARACTERES_VARIAVEL,
) -> TemplatePreenchido:
    valores: dict[str, str] = {}
    fallbacks: list[str] = []
    for variavel in template.variaveis:
        limite = min(variavel.max_caracteres or max_caracteres, max_caracteres)
        bruto: str | None
        if variavel.gancho:
            bruto = contexto.ganchos.get(variavel.nome)
        elif variavel.preencher is not None:
            try:
                bruto = variavel.preencher(contexto)
            except (KeyError, TypeError, ValueError, AttributeError):
                bruto = None
        else:
            bruto = None
        valor = validar_variavel(bruto, limite)
        if valor is None:
            valor = variavel.padrao
            fallbacks.append(variavel.nome)
        valores[variavel.nome] = valor
    return TemplatePreenchido(template, valores, tuple(fallbacks))
