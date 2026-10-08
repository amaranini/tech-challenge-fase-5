"""Resumo de handoff: o que o responsável precisa saber antes de atender o lead.

O template (seções, títulos e instruções) é da vertical; o core garante a ANCORAGEM:
- seções de dados (ficha, score, agendamento) vêm do estado do lead, nunca do LLM;
- seções redigidas pelo LLM só ficam com o que tem lastro na conversa: item do catálogo
  só se foi citado na conversa; objeção/pergunta só com evidência literal do lead; trecho
  só se existir palavra por palavra;
- o que ficou vazio aparece como "não informado". Nada é inventado nem completado.

Resumos são versionados: uma nova versão só nasce quando a "impressão digital" dos fatos
(ficha, score, agendamento, itens e mensagens) muda.
"""

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sdr.core.domain.agenda import Agendamento
from sdr.core.domain.conversa import Mensagem, Papel
from sdr.core.domain.qualificacao import Ficha, Score

NAO_INFORMADO = "não informado"
MINIMO_EVIDENCIA = 3  # caracteres: evita "sim" casando com qualquer fala


class TipoSecao(StrEnum):
    # redigidas pelo LLM (com ancoragem)
    TEXTO = "texto"  # parágrafo curto
    LISTA = "lista"  # itens com evidência literal do lead
    ITENS_CATALOGO = "itens_catalogo"  # itens citados na conversa + reação do lead
    TRECHOS = "trechos"  # falas literais da conversa
    # preenchidas pelo core a partir do estado do lead
    FICHA = "ficha"
    SCORE = "score"
    AGENDAMENTO = "agendamento"


SECOES_DE_DADOS = frozenset({TipoSecao.FICHA, TipoSecao.SCORE, TipoSecao.AGENDAMENTO})


@dataclass(frozen=True)
class SecaoResumo:
    chave: str
    titulo: str
    tipo: TipoSecao
    instrucao: str = ""  # para o LLM (seções redigidas)


@dataclass(frozen=True)
class TemplateResumo:
    """Declarado pela vertical: para quem é o resumo e quais seções ele tem."""

    versao: str
    titulo: str
    secoes: tuple[SecaoResumo, ...]
    instrucoes: str = ""  # orientação geral (público, tom)

    @property
    def redigidas(self) -> tuple[SecaoResumo, ...]:
        return tuple(s for s in self.secoes if s.tipo not in SECOES_DE_DADOS)


@dataclass(frozen=True)
class ItemCitado:
    id: str
    titulo: str
    resumo: str = ""


@dataclass(frozen=True)
class FatosResumo:
    """Tudo o que o resumo pode afirmar — e nada além disso."""

    lead_id: UUID
    intencao: str | None
    ficha: Ficha
    campos_faltantes: tuple[str, ...]
    score: Score | None
    proxima_acao: str | None
    agendamento: Agendamento | None
    mensagens: tuple[Mensagem, ...]  # conversa em ordem cronológica
    itens: Mapping[str, ItemCitado]  # itens do catálogo citados na conversa (por id)
    mensagens_espera: tuple[str, ...] = ()  # falas do lead enquanto aguardava um humano

    def impressao_digital(self) -> str:
        ag = self.agendamento
        base = {
            "intencao": self.intencao,
            "ficha": self.ficha,
            "score": [self.score.pontos, self.score.classificacao.value] if self.score else None,
            "proxima_acao": self.proxima_acao,
            "agendamento": [str(ag.id), ag.inicio.isoformat(), ag.status.value] if ag else None,
            "itens": sorted(self.itens),
            "mensagens": [str(m.id) for m in self.mensagens if m.papel is Papel.LEAD],
        }
        bruto = json.dumps(base, sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256(bruto.encode()).hexdigest()


@dataclass(frozen=True)
class SecaoPreenchida:
    chave: str
    titulo: str
    tipo: TipoSecao
    conteudo: object  # str | list | dict; NAO_INFORMADO quando vazio


@dataclass(frozen=True)
class Resumo:
    id: UUID
    lead_id: UUID
    versao: int
    gerado_em: datetime
    gatilho: str  # tipo do evento que pediu o resumo
    template_versao: str
    titulo: str
    secoes: tuple[SecaoPreenchida, ...]
    impressao: str
    descartados: tuple[str, ...] = ()  # o que a ancoragem removeu (auditoria)
    modelo: str | None = None
    tokens_entrada: int = 0
    tokens_saida: int = 0

    def secao(self, chave: str) -> SecaoPreenchida | None:
        return next((s for s in self.secoes if s.chave == chave), None)


# ---------------------------------------------------------------------- ancoragem


def _normalizar(texto: str) -> str:
    texto = re.sub(r"\s+", " ", texto.casefold()).strip()
    return texto.strip(" \"'“”‘’.,;:!?…")


def _falas(fatos: FatosResumo, papel: Papel | None = None) -> str:
    return " \n ".join(
        _normalizar(m.texto) for m in fatos.mensagens if papel is None or m.papel is papel
    )


def tem_lastro(evidencia: object, fatos: FatosResumo, papel: Papel | None = Papel.LEAD) -> bool:
    """A evidência aparece, palavra por palavra (sem caixa/pontuação nas pontas), nas falas."""
    if not isinstance(evidencia, str):
        return False
    alvo = _normalizar(evidencia)
    return len(alvo) >= MINIMO_EVIDENCIA and alvo in _falas(fatos, papel)


def _texto(bruto: object) -> str:
    return bruto.strip() if isinstance(bruto, str) and bruto.strip() else NAO_INFORMADO


def _lista(bruto: object, fatos: FatosResumo, descartados: list[str], chave: str) -> object:
    itens = []
    for entrada in bruto if isinstance(bruto, list) else []:
        if not isinstance(entrada, Mapping) or _texto(entrada.get("texto")) == NAO_INFORMADO:
            continue
        if tem_lastro(entrada.get("evidencia"), fatos):
            itens.append(
                {"texto": str(entrada["texto"]).strip(), "evidencia": entrada["evidencia"]}
            )
        else:
            descartados.append(f"{chave}: sem lastro na fala do lead — {entrada.get('texto')!r}")
    return itens or NAO_INFORMADO


def _itens_catalogo(
    bruto: object, fatos: FatosResumo, descartados: list[str], chave: str
) -> object:
    reacoes: dict[str, str] = {}
    for entrada in bruto if isinstance(bruto, list) else []:
        if not isinstance(entrada, Mapping):
            continue
        item_id = str(entrada.get("id", "")).strip()
        if item_id not in fatos.itens:
            descartados.append(f"{chave}: item {item_id!r} não foi citado na conversa")
            continue
        reacao = _texto(entrada.get("reacao"))
        if reacao != NAO_INFORMADO and not tem_lastro(entrada.get("evidencia"), fatos):
            descartados.append(f"{chave}: reação a {item_id} sem lastro — {reacao!r}")
            reacao = NAO_INFORMADO
        reacoes.setdefault(item_id, reacao)
    # Todo item citado aparece, mesmo que o LLM tenha esquecido (reação: não informado).
    return [
        {"id": i.id, "titulo": i.titulo, "reacao": reacoes.get(i.id, NAO_INFORMADO)}
        for i in fatos.itens.values()
    ] or NAO_INFORMADO


def _trechos(bruto: object, fatos: FatosResumo, descartados: list[str], chave: str) -> object:
    trechos = []
    for entrada in bruto if isinstance(bruto, list) else []:
        texto = entrada.get("texto") if isinstance(entrada, Mapping) else entrada
        if tem_lastro(texto, fatos):
            trechos.append(str(texto).strip())
        else:
            descartados.append(f"{chave}: trecho inexistente na conversa — {texto!r}")
    return trechos or NAO_INFORMADO


def _dados(secao: SecaoResumo, fatos: FatosResumo) -> object:
    if secao.tipo is TipoSecao.FICHA:
        campos: dict[str, object] = {
            c: v for c, v in fatos.ficha.items() if v not in (None, "", [], {})
        }
        for faltante in fatos.campos_faltantes:
            campos.setdefault(faltante, NAO_INFORMADO)
        return campos or NAO_INFORMADO
    if secao.tipo is TipoSecao.SCORE:
        s = fatos.score
        if s is None:
            return NAO_INFORMADO
        return {
            "pontos": s.pontos,
            "classificacao": s.classificacao.value,
            "motivos": list(s.motivos),
        }
    ag = fatos.agendamento
    if ag is None:
        return NAO_INFORMADO
    return {
        "tipo": ag.tipo,
        "inicio": ag.inicio.isoformat(),
        "modalidade": ag.modalidade,
        "status": ag.status.value,
        "responsavel": ag.responsavel.nome,
        "responsavel_titulo": ag.responsavel.titulo,
    }


def ancorar(
    bruto: Mapping[str, object], template: TemplateResumo, fatos: FatosResumo
) -> tuple[tuple[SecaoPreenchida, ...], tuple[str, ...]]:
    """Monta as seções do template: dados vêm dos fatos; o que o LLM redigiu só fica se
    tiver lastro. Devolve (seções, o que foi descartado e por quê)."""
    descartados: list[str] = []
    secoes = []
    for secao in template.secoes:
        valor = bruto.get(secao.chave)
        conteudo: object
        match secao.tipo:
            case TipoSecao.TEXTO:
                conteudo = _texto(valor)
            case TipoSecao.LISTA:
                conteudo = _lista(valor, fatos, descartados, secao.chave)
            case TipoSecao.ITENS_CATALOGO:
                conteudo = _itens_catalogo(valor, fatos, descartados, secao.chave)
            case TipoSecao.TRECHOS:
                conteudo = _trechos(valor, fatos, descartados, secao.chave)
            case _:
                conteudo = _dados(secao, fatos)
        secoes.append(SecaoPreenchida(secao.chave, secao.titulo, secao.tipo, conteudo))
    return tuple(secoes), tuple(descartados)


CHAVE_MENSAGENS_ESPERA = "mensagens_na_espera"


def anexar_mensagens_espera(
    secoes: tuple[SecaoPreenchida, ...], fatos: FatosResumo
) -> tuple[SecaoPreenchida, ...]:
    """Falas do lead enquanto aguardava na fila vão literais para quem vai atendê-lo."""
    if not fatos.mensagens_espera:
        return secoes
    anexo = SecaoPreenchida(
        CHAVE_MENSAGENS_ESPERA,
        "Mensagens do lead enquanto aguardava atendimento",
        TipoSecao.TRECHOS,
        list(fatos.mensagens_espera),
    )
    return (*secoes, anexo)


def itens_citados(mensagens: Sequence[Mensagem]) -> dict[str, ItemCitado]:
    """Itens do catálogo que o assistente apresentou na conversa (metadados das mensagens)."""
    itens: dict[str, ItemCitado] = {}
    for mensagem in mensagens:
        citados = mensagem.metadados.get("itens_citados")
        for c in citados if isinstance(citados, list) else []:
            if isinstance(c, Mapping) and c.get("id"):
                itens.setdefault(
                    str(c["id"]),
                    ItemCitado(str(c["id"]), str(c.get("titulo", "")), str(c.get("resumo", ""))),
                )
    return itens


class FonteAfirmacao(StrEnum):
    CONVERSA = "conversa"  # algo que o lead (ou o assistente) disse — exige evidência literal
    DADOS = "dados"  # ficha, score ou agendamento
    RECOMENDACAO = "recomendacao"  # ação sugerida, sem afirmar nada novo sobre o lead


@dataclass(frozen=True)
class AfirmacaoChecada:
    """Uma frase de seção de texto livre, julgada contra os fatos (pelo verificador)."""

    frase: str
    sustentada: bool
    fonte: FonteAfirmacao
    evidencias: tuple[str, ...] = ()  # trechos literais (um por fala) quando fonte = conversa
    motivo: str = ""  # por que não é sustentada (auditoria)


def _frase_sustentada(afirmacao: AfirmacaoChecada, original: str, fatos: FatosResumo) -> bool:
    if not afirmacao.sustentada or _normalizar(afirmacao.frase) not in _normalizar(original):
        return False  # verificador negou, ou reescreveu a frase em vez de citá-la
    if afirmacao.fonte is FonteAfirmacao.CONVERSA:
        evidencias = afirmacao.evidencias
        return bool(evidencias) and all(tem_lastro(e, fatos, papel=None) for e in evidencias)
    return True


def podar_textos(
    secoes: Sequence[SecaoPreenchida],
    checagem: Mapping[str, Sequence[AfirmacaoChecada]],
    fatos: FatosResumo,
) -> tuple[tuple[SecaoPreenchida, ...], tuple[str, ...]]:
    """Seções de texto livre ficam só com as frases sustentadas. Sem checagem, a seção vira
    "não informado" (na dúvida, não afirmar)."""
    descartados: list[str] = []
    resultado = []
    for secao in secoes:
        if secao.tipo is not TipoSecao.TEXTO or secao.conteudo == NAO_INFORMADO:
            resultado.append(secao)
            continue
        original = str(secao.conteudo)
        mantidas = []
        for afirmacao in checagem.get(secao.chave, ()):
            if _frase_sustentada(afirmacao, original, fatos):
                mantidas.append(afirmacao.frase.strip())
            else:
                motivo = f" ({afirmacao.motivo})" if afirmacao.motivo else ""
                descartados.append(
                    f"{secao.chave}: frase sem sustentação{motivo} — {afirmacao.frase!r}"
                )
        if not checagem.get(secao.chave):
            descartados.append(f"{secao.chave}: texto não verificado — {original!r}")
        texto = " ".join(mantidas) or NAO_INFORMADO
        resultado.append(SecaoPreenchida(secao.chave, secao.titulo, secao.tipo, texto))
    return tuple(resultado), tuple(descartados)


@dataclass(frozen=True)
class RascunhoResumo:
    """Saída bruta do redator (LLM), antes da ancoragem."""

    secoes: Mapping[str, object] = field(default_factory=dict)
    modelo: str | None = None
    tokens_entrada: int = 0
    tokens_saida: int = 0
