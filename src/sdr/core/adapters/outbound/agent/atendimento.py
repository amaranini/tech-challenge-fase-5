"""Atendimento humano no grafo: classificação das respostas do lead e o bloco de instruções
da resposta. As transições são do domínio (`Atendimento`); aqui só se decide a AÇÃO do
turno e se redige o que dizer."""

from collections.abc import Mapping
from datetime import datetime

from sdr.core.domain.atendimento import (
    AcaoAtendimento,
    Atendimento,
    EstadoAtendimento,
    HorarioAtendimento,
    MotivoHandoff,
    TipoAcaoAtendimento,
    descrever_retorno,
)

SEM_HANDOFF = "nao"
SINAIS_HUMANO = [SEM_HANDOFF, *(m.value for m in MotivoHandoff)]
SITUACAO_HANDOFF = (
    "A atendente perguntou se o lead quer ser transferido para uma pessoa da equipe (entrar "
    "na fila de atendimento humano). 'sim': ele aceita a transferência. 'nao': recusa ou diz "
    "que prefere seguir com a assistente. 'ambigua': não dá para saber."
)
SITUACAO_RETORNO = (
    "Você perguntou ao lead se ele confirma que quer voltar a conversar com a assistente "
    "virtual, saindo da fila de atendimento humano. Ele confirmou (sim), recusou (nao) ou a "
    "resposta é ambígua?"
)
SITUACAO_ESPERA = (
    "O lead está na fila aguardando uma pessoa da equipe. Marque true SÓ se ele pedir "
    "EXPLICITAMENTE para voltar a conversar com a assistente virtual ou desistir de esperar "
    "(ex.: 'pode ser com você mesmo', 'quero voltar pro robô', 'não quero mais esperar'). "
    "Cumprimentos e cobranças ('oi?', 'alguém aí?', 'e aí, demora?') e perguntas são o lead "
    "ESPERANDO: false."
)

SCHEMA_RESPOSTA: dict[str, object] = {
    "type": "object",
    "properties": {"resposta": {"type": "string", "enum": ["sim", "nao", "ambigua"]}},
    "required": ["resposta"],
    "additionalProperties": False,
}
SCHEMA_ESPERA: dict[str, object] = {
    "type": "object",
    "properties": {"quer_voltar_para_assistente": {"type": "boolean"}},
    "required": ["quer_voltar_para_assistente"],
    "additionalProperties": False,
}


def ler_confirmacao(dados: Mapping[str, object]) -> bool | None:
    return {"sim": True, "nao": False}.get(str(dados.get("resposta")))


def ler_sinal_humano(valor: object) -> MotivoHandoff | None:
    try:
        return MotivoHandoff(str(valor))
    except ValueError:
        return None


def _horario(horario: HorarioAtendimento, agora: datetime) -> str:
    if horario.aberto(agora):
        return ""
    retorno = descrever_retorno(horario.proxima_abertura(agora), agora, horario.fuso)
    return (
        f" A equipe está FORA DO HORÁRIO agora: o atendimento é {horario.descrever()} e a "
        f"equipe volta {retorno}."
    )


MOTIVOS = {
    MotivoHandoff.PEDIDO_EXPLICITO: "pediu para falar com uma pessoa da equipe",
    MotivoHandoff.FRUSTRACAO: "parece frustrado com o atendimento — seja empática",
    MotivoHandoff.FORA_DO_ESCOPO: "pediu algo que precisa de uma pessoa da equipe",
    MotivoHandoff.NEGOCIACAO: "quer negociar condições, o que exige uma pessoa da equipe",
}


def bloco_atendimento(
    acao: AcaoAtendimento,
    antes: Atendimento,
    depois: Atendimento,
    horario: HorarioAtendimento,
    agora: datetime,
) -> str:
    fora = _horario(horario, agora)
    linhas = ["[Atendimento — uso interno, nunca mostre isto ao lead]"]
    match acao.tipo, depois.estado:
        case TipoAcaoAtendimento.SOLICITAR_HANDOFF, _:
            motivo = MOTIVOS[acao.motivo or MotivoHandoff.PEDIDO_EXPLICITO]
            linhas.append(
                f"O lead {motivo}."
                + (
                    f"{fora} Informe isso e diga, numa frase afirmativa, que enquanto isso "
                    "você pode seguir ajudando."
                    if fora
                    else ""
                )
                + " Termine com UMA pergunta de sim/não: se ele quer ser transferido para uma "
                "pessoa da equipe (entrando na fila de atendimento). NÃO pergunte se ele quer "
                "continuar com você — isso deixa o 'sim' ambíguo."
            )
        case TipoAcaoAtendimento.RESPONDER_HANDOFF, EstadoAtendimento.AGUARDANDO_HUMANO:
            linhas.append(
                "O lead confirmou e ENTROU NA FILA. Diga que uma pessoa da equipe vai continuar "
                "o atendimento por aqui, nesta conversa." + fora + " Não faça perguntas."
            )
        case TipoAcaoAtendimento.RESPONDER_HANDOFF, EstadoAtendimento.CONFIRMANDO_HANDOFF:
            linhas.append(
                "A resposta foi ambígua. Pergunte de novo, bem direto (sim ou não), se ele "
                "quer ser transferido para uma pessoa da equipe."
            )
        case TipoAcaoAtendimento.RESPONDER_HANDOFF, _:
            if acao.confirmado is False:
                linhas.append(
                    "O lead preferiu continuar com você. Confirme com leveza e retome o "
                    "assunto de onde parou (veja o histórico)."
                )
            else:
                linhas.append(
                    "Sem confirmação clara: o lead segue com você. Diga isso com leveza (se "
                    "quiser uma pessoa da equipe, é só pedir) e retome o assunto."
                )
        case TipoAcaoAtendimento.MENSAGEM_NA_ESPERA, _:
            linhas.append(
                "O lead está NA FILA aguardando uma pessoa da equipe. Diga que ele continua "
                "na fila e que a equipe vai responder por aqui." + fora + " Pergunte se ele "
                "prefere voltar a conversar com a assistente virtual enquanto isso. Se ele "
                "perguntou algo, diga que a equipe responde (ou que você responde se ele "
                "voltar para a assistente)."
            )
        case TipoAcaoAtendimento.SOLICITAR_RETORNO_IA, _:
            linhas.append(
                "O lead quer voltar a conversar com a assistente virtual. Peça confirmação "
                "EXPLÍCITA: ao voltar, ele SAI da fila de atendimento humano."
            )
        case TipoAcaoAtendimento.RESPONDER_RETORNO_IA, EstadoAtendimento.ATENDIMENTO_IA:
            linhas.append(
                "O lead SAIU DA FILA e voltou a conversar com você. Confirme e retome a "
                "conversa do ponto onde ela parou (veja o histórico), com uma pergunta útil."
            )
        case TipoAcaoAtendimento.RESPONDER_RETORNO_IA, EstadoAtendimento.CONFIRMANDO_RETORNO_IA:
            linhas.append(
                "A resposta foi ambígua. Pergunte de novo, bem direto (sim ou não), se ele "
                "quer sair da fila e voltar para a assistente virtual."
            )
        case _:
            linhas.append(
                "O lead continua NA FILA aguardando uma pessoa da equipe: confirme isso com "
                "leveza." + fora
            )
    if antes.na_fila_desde and depois.na_fila:
        minutos = antes.espera(agora) // 60
        linhas.append(f"(Na fila há {minutos} min — não cite tempo de espera ao lead.)")
    return "\n".join(linhas)
