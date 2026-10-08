"""RedatorResumoPort com LLM: redige as seções do template em saída estruturada.

Só redige: os dados (ficha, score, agendamento) vão como contexto e a ancoragem do domínio
descarta o que não tiver lastro na conversa.
"""

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from zoneinfo import ZoneInfo

from sdr.core.application.ports.llm import LLMPort, MensagemLLM, PapelLLM
from sdr.core.domain.agenda import DIAS_SEMANA
from sdr.core.domain.conversa import Papel
from sdr.core.domain.resumo import (
    AfirmacaoChecada,
    FatosResumo,
    FonteAfirmacao,
    RascunhoResumo,
    SecaoResumo,
    TemplateResumo,
    TipoSecao,
)

PASTA_PROMPTS = Path(__file__).resolve().parent / "prompts"
PROMPT_RESUMO = (PASTA_PROMPTS / "resumo_v1.md").read_text(encoding="utf-8")
PROMPT_CHECAGEM = (PASTA_PROMPTS / "resumo_checagem_v1.md").read_text(encoding="utf-8")


def _schema_secao(secao: SecaoResumo, ids_itens: list[str]) -> dict[str, object]:
    texto: dict[str, object] = {"type": "string"}
    match secao.tipo:
        case TipoSecao.LISTA:
            item: dict[str, object] = {
                "type": "object",
                "properties": {"texto": texto, "evidencia": texto},
                "required": ["texto", "evidencia"],
                "additionalProperties": False,
            }
            return {"type": "array", "items": item}
        case TipoSecao.ITENS_CATALOGO:
            ids = {"type": "string", "enum": ids_itens} if ids_itens else texto
            item = {
                "type": "object",
                "properties": {"id": ids, "reacao": texto, "evidencia": texto},
                "required": ["id", "reacao", "evidencia"],
                "additionalProperties": False,
            }
            return {"type": "array", "items": item}
        case TipoSecao.TRECHOS:
            return {"type": "array", "items": texto}
        case _:
            return texto


def schema_resumo(template: TemplateResumo, fatos: FatosResumo) -> dict[str, object]:
    ids = sorted(fatos.itens)
    secoes = template.redigidas
    return {
        "type": "object",
        "properties": {s.chave: _schema_secao(s, ids) for s in secoes},
        "required": [s.chave for s in secoes],
        "additionalProperties": False,
    }


def _dados(fatos: FatosResumo, fuso: ZoneInfo) -> str:
    ag = fatos.agendamento
    quando = None
    if ag is not None:
        local = ag.inicio.astimezone(fuso)
        quando = f"{DIAS_SEMANA[local.weekday()]}, {local:%d/%m/%Y} às {local:%H:%M}"
    dados = {
        "intencao": fatos.intencao,
        "ficha": dict(fatos.ficha),
        "campos_nao_informados": list(fatos.campos_faltantes),
        "score": (
            {"pontos": fatos.score.pontos, "motivos": list(fatos.score.motivos)}
            if fatos.score
            else None
        ),
        "proxima_acao": fatos.proxima_acao,
        "agendamento": (
            {
                "tipo": ag.tipo,
                "quando": quando,
                "modalidade": ag.modalidade,
                "status": ag.status.value,
                "com": f"{ag.responsavel.nome} ({ag.responsavel.titulo})",
            }
            if ag
            else None
        ),
        "itens_citados": [
            {"id": i.id, "titulo": i.titulo, "resumo": i.resumo} for i in fatos.itens.values()
        ],
    }
    return json.dumps(dados, ensure_ascii=False, indent=1, default=str)


def _transcricao(fatos: FatosResumo) -> str:
    linhas = [
        f"{'Lead' if m.papel is Papel.LEAD else 'Assistente'}: {m.texto}" for m in fatos.mensagens
    ]
    return "Conversa completa:\n" + "\n".join(linhas)


class RedatorResumoLLM:
    def __init__(self, llm: LLMPort, fuso: ZoneInfo) -> None:
        self._llm = llm
        self._fuso = fuso

    async def redigir(self, fatos: FatosResumo, template: TemplateResumo) -> RascunhoResumo:
        secoes = "\n".join(f"- {s.chave}: {s.titulo} — {s.instrucao}" for s in template.redigidas)
        sistema = PROMPT_RESUMO.format(
            instrucoes=template.instrucoes, secoes=secoes, dados=_dados(fatos, self._fuso)
        )
        resposta = await self._llm.gerar_estruturado(
            [
                MensagemLLM(PapelLLM.SISTEMA, sistema),
                MensagemLLM(PapelLLM.USUARIO, _transcricao(fatos)),
            ],
            schema_resumo(template, fatos),
            "resumo_handoff",
        )
        dados: Mapping[str, object] = resposta.dados if isinstance(resposta.dados, Mapping) else {}
        return RascunhoResumo(
            dict(dados), resposta.modelo, resposta.tokens_entrada, resposta.tokens_saida
        )

    async def checar(
        self, fatos: FatosResumo, textos: Mapping[str, str]
    ) -> Mapping[str, Sequence[AfirmacaoChecada]]:
        frase: dict[str, object] = {
            "type": "object",
            "properties": {
                "frase": {"type": "string"},
                "fonte": {"type": "string", "enum": [f.value for f in FonteAfirmacao]},
                "sustentada": {"type": "boolean"},
                "evidencias": {"type": "array", "items": {"type": "string"}},
                "motivo": {"type": "string"},
            },
            "required": ["frase", "fonte", "sustentada", "evidencias", "motivo"],
            "additionalProperties": False,
        }
        schema = {
            "type": "object",
            "properties": {chave: {"type": "array", "items": frase} for chave in textos},
            "required": list(textos),
            "additionalProperties": False,
        }
        a_verificar = "\n".join(f"[{chave}] {texto}" for chave, texto in textos.items())
        resposta = await self._llm.gerar_estruturado(
            [
                MensagemLLM(
                    PapelLLM.SISTEMA, PROMPT_CHECAGEM.format(dados=_dados(fatos, self._fuso))
                ),
                MensagemLLM(
                    PapelLLM.USUARIO,
                    f"{_transcricao(fatos)}\n\nTextos a verificar:\n{a_verificar}",
                ),
            ],
            schema,
            "checagem_resumo",
        )
        return {chave: _afirmacoes(resposta.dados.get(chave)) for chave in textos}


def _afirmacoes(bruto: object) -> list[AfirmacaoChecada]:
    afirmacoes = []
    for item in bruto if isinstance(bruto, list) else []:
        if not isinstance(item, Mapping) or not isinstance(item.get("frase"), str):
            continue
        try:
            fonte = FonteAfirmacao(str(item.get("fonte")))
        except ValueError:
            continue
        afirmacoes.append(
            AfirmacaoChecada(
                frase=item["frase"],
                sustentada=item.get("sustentada") is True,
                fonte=fonte,
                evidencias=_trechos(item.get("evidencias")),
                motivo=str(item.get("motivo") or ""),
            )
        )
    return afirmacoes


AUTORES = ("lead", "assistente", "atendente")


def _trechos(bruto: object) -> tuple[str, ...]:
    """Um trecho por fala, sem o prefixo de autor que o modelo às vezes copia ("Lead: ...")
    e sem juntar várias falas numa string só (aí nada é literal)."""
    trechos = []
    for valor in bruto if isinstance(bruto, list) else [bruto]:
        for linha in str(valor or "").splitlines():
            autor, separador, resto = linha.partition(":")
            com_autor = separador and autor.strip().casefold() in AUTORES
            trecho = (resto if com_autor else linha).strip()
            if trecho:
                trechos.append(trecho)
    return tuple(trechos)
