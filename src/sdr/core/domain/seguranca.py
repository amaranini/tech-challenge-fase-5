"""Guardrails puros do agente: detector leve de prompt injection e promessas indevidas.

A heurística é a PRIMEIRA linha (barata, determinística, sem LLM); o roteador também
classifica a fala (`tentativa_injecao`) e basta um dos dois para o turno seguir o caminho
seguro. Padrões genéricos, em português e inglês — a vertical pode acrescentar os seus
(ex.: promessas típicas do segmento) pela Persona.

Nenhum padrão aqui é "a defesa": o controle que impede vazamento é estrutural (o agente
não tem ferramenta que leia outros leads). A detecção serve para manter a persona,
registrar a tentativa (evento) e não deixar a fala contaminar a ficha.
"""

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass

# (nome do padrão, regex sobre o texto normalizado: minúsculo e sem acento)
PADROES_INJECAO: tuple[tuple[str, str], ...] = (
    (
        "ignorar_instrucoes",
        r"\b(ignor[ea]m?|esquec[ae]m?|desconsider[ea]m?|desobedec[ae]m?|anule)\b.{0,30}"
        r"\b(instruc\w*|regras|ordens|prompt|orientac\w*|diretriz\w*|comandos)\b",
    ),
    (
        "ignore_instructions",
        r"\b(ignore|disregard|forget|override)\b.{0,40}"
        r"\b(instructions?|rules|prompt|guidelines|directives)\b",
    ),
    (
        "revelar_prompt",
        r"\b(mostr\w*|revel\w*|exib\w*|imprim\w*|repit\w*|copi\w*|qual (e|sao))\b.{0,30}"
        r"\b(seu prompt|o prompt|suas instruc\w*|suas regras|instruc\w* (do|de) sistema)\b",
    ),
    (
        "system_prompt",
        r"\b(system prompt|prompt do sistema|developer message|mensagem do sistema)\b",
    ),
    (
        "trocar_papel",
        r"\b(a partir de agora|de agora em diante)\b.{0,20}\b(voce (e|sera|vai ser)|aja|seja|"
        r"responda como)\b|\b(finja (ser|que voce)|you are now|act as|pretend to be)\b",
    ),
    (
        "modo_especial",
        r"\b(modo (desenvolvedor|dev|admin|administrador|debug|deus)|jailbreak|dan mode|"
        r"developer mode)\b",
    ),
    (
        "marcacao_de_papel",
        r"(<\s*/?\s*(system|assistant|developer|instructions?)\s*>|\[/?(system|inst)\]|"
        r"^\s*(system|assistant)\s*:)",
    ),
    (
        "extrair_dados",
        r"\b(list\w*|mostr\w*|export\w*|envi\w*|traga|quais sao|"
        r"me (de|da|dar|diga|dizer|passa|passar|manda|mandar|informa|informar))\b"
        r".{0,30}\b(todos|todas|outros|outras|demais)\b.{0,20}\b(leads?|clientes|contatos|"
        r"usuarios|conversas|telefones|e-?mails|cpfs)\b",
    ),
    ("dump_banco", r"\b(select \* from|drop table|database dump|base de clientes)\b"),
)

# Promessas que a IA nunca pode fazer: preço/condição só o que está na base.
PADROES_PROMESSA: tuple[tuple[str, str], ...] = (
    (
        "desconto",
        r"\b(consigo|garanto|dou|darei|ofereco|posso (te )?(dar|oferecer|conseguir)|"
        r"vou (te )?(dar|conseguir))\b.{0,30}\bdesconto\b|\bdesconto de \d",
    ),
    (
        "baixar_preco",
        r"\b(consigo|posso|vou|garanto)\b.{0,20}\b(baixar|reduzir|abaixar)\b.{0,20}"
        r"\b(preco|valor)\b",
    ),
    (
        "garantia_resultado",
        r"\b(aprovacao|rendimento|retorno|valorizacao|lucro)\b.{0,15}\bgarantid[oa]s?\b",
    ),
    ("garanto_que", r"\bgaranto (que|a voce|pra voce|para voce)\b"),
)


@dataclass(frozen=True)
class SinalInjecao:
    """Tentativa de injeção percebida na fala do lead (a fala em si não é guardada)."""

    origens: tuple[str, ...]  # "heuristica", "llm"
    padroes: tuple[str, ...] = ()

    @property
    def detectada(self) -> bool:
        return bool(self.origens)


def normalizar(texto: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return " ".join(sem_acento.lower().split())


def _casar(padroes: Iterable[tuple[str, str]], texto: str) -> tuple[str, ...]:
    alvo = normalizar(texto)
    return tuple(nome for nome, regex in padroes if re.search(regex, alvo, re.MULTILINE))


def padroes_injecao(texto: str) -> tuple[str, ...]:
    """Nomes dos padrões de injeção encontrados na fala (vazio = nada suspeito)."""
    return _casar(PADROES_INJECAO, texto)


def avaliar_injecao(texto: str, *, classificada_pelo_llm: bool) -> SinalInjecao | None:
    """Junta a heurística e a classificação do roteador: basta um dos dois."""
    padroes = padroes_injecao(texto)
    origens = (("heuristica",) if padroes else ()) + (("llm",) if classificada_pelo_llm else ())
    return SinalInjecao(origens, padroes) if origens else None


# Negação logo antes do trecho: "não consigo dar desconto" é o comportamento CERTO (a regex
# casava e a resposta era regenerada à toa — visto na avaliação do Dia 5).
_NEGACAO_ANTES = re.compile(r"\b(nao|nem|nunca|jamais|sem)\b[^.!?:;,]{0,15}$")


def promessas_indevidas(texto: str, extras: Iterable[tuple[str, str]] = ()) -> tuple[str, ...]:
    """Promessas comerciais fora da base na RESPOSTA do agente (desconto, garantia...),
    ignorando as negadas ("não consigo desconto", "não garanto que...")."""
    alvo = normalizar(texto)
    return tuple(
        nome
        for nome, regex in (*PADROES_PROMESSA, *extras)
        if any(
            not _NEGACAO_ANTES.search(alvo[: m.start()])
            for m in re.finditer(regex, alvo, re.MULTILINE)
        )
    )
