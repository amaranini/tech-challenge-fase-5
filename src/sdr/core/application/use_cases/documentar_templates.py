"""Documento de submissão dos templates lógicos ao provedor (aprovação na Meta).

Gerado a partir do que a vertical declara — o arquivo em docs/ nunca é editado à mão
(`python -m sdr.cli templates-doc`; um teste confere que está em dia).
"""

import json
from collections.abc import Mapping, Sequence

from sdr.core.domain.template import MAX_CARACTERES_VARIAVEL, TemplateLogico

CABECALHO = """# Templates do WhatsApp — textos para aprovação na Meta

> Gerado por `python -m sdr.cli templates-doc` a partir dos templates lógicos da vertical
> `{vertical}`. Não edite à mão: mude o template na vertical e gere de novo.

Fora da janela de 24h desde a última mensagem do lead, o WhatsApp só aceita mensagem
iniciada pela empresa com **template aprovado**. Para cada template abaixo:

1. Crie o template no provedor (Twilio: *Content Template Builder*, tipo *text*; ou no
   WhatsApp Manager) com o **nome**, a **categoria**, o **idioma** e o **corpo numerado**.
2. Use os **exemplos** da tabela como valores de amostra das variáveis.
3. Aprovado, mapeie o nome lógico → template no `.env` da operação
   (`WHATSAPP_TEMPLATES`, JSON), mantendo a ordem das variáveis do corpo numerado.

Enquanto um nome lógico não estiver mapeado, nada sai como texto livre fora da janela: a
mensagem fica como não enviada e o evento `EnvioTemplateIndisponivel` é registrado.

Regras do validador (core): toda variável é uma linha só, sem tabulação, sem 4+ espaços
seguidos, não vazia e com no máximo {maximo} caracteres (ou o limite da variável). Valor
recusado ou dado ausente ⇒ valor padrão. Variáveis *gancho* são escritas pelo LLM e
passam pelo mesmo validador.
"""


def documentar_templates(
    templates: Sequence[TemplateLogico],
    *,
    vertical: str,
    usos: Mapping[str, Sequence[str]] | None = None,
    max_caracteres: int = MAX_CARACTERES_VARIAVEL,
) -> str:
    partes = [CABECALHO.format(vertical=vertical, maximo=max_caracteres)]
    for template in templates:
        partes.append(_secao(template, (usos or {}).get(template.nome, ()), max_caracteres))
    partes.append(_config(templates))
    return "\n".join(partes).rstrip() + "\n"


def _secao(template: TemplateLogico, usos: Sequence[str], maximo: int) -> str:
    exemplos = {v.nome: v.exemplo or v.padrao for v in template.variaveis}
    linhas = [
        f"## `{template.nome}`",
        "",
        f"- **Categoria:** {template.categoria.value}",
        f"- **Idioma:** {template.idioma}",
    ]
    if usos:
        linhas.append(f"- **Usado em:** {'; '.join(usos)}")
    linhas += [
        "",
        "**Corpo para submissão (numerado):**",
        "",
        "```text",
        template.numerado(),
        "```",
        "",
        "**Texto de referência (variáveis nomeadas):**",
        "",
        "```text",
        template.texto_referencia,
        "```",
        "",
        f"**Exemplo:** {template.renderizar(exemplos)}",
        "",
        "| Nº | Variável | Regra de preenchimento | Padrão (fallback) | Exemplo | Limite |",
        "|---|---|---|---|---|---|",
    ]
    for numero, variavel in enumerate(template.variaveis, start=1):
        origem = " _(gancho — LLM)_" if variavel.gancho else ""
        limite = min(variavel.max_caracteres or maximo, maximo)
        linhas.append(
            f"| {{{{{numero}}}}} | `{variavel.nome}` | {variavel.descricao}{origem} | "
            f"{variavel.padrao} | {exemplos[variavel.nome]} | {limite} |"
        )
    return "\n".join(linhas) + "\n"


def _config(templates: Sequence[TemplateLogico]) -> str:
    exemplo = {
        t.nome: {"content_sid": "HX...", "idioma": t.idioma, "variaveis": list(t.ordem)}
        for t in templates
    }
    return "\n".join(
        [
            "## Mapeamento na operação (`.env`)",
            "",
            "Em uma linha só no `.env` (aqui formatado para leitura):",
            "",
            "```json",
            json.dumps(exemplo, ensure_ascii=False, indent=2),
            "```",
        ]
    )
