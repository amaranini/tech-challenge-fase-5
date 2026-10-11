"""Agenda no grafo: leitura da interpretação do LLM e o bloco de instruções da resposta.

O LLM só interpreta a fala do lead (saída estruturada) e redige a mensagem; o que é
oferecido, proposto ou executado vem de `ConduzirAgendamento`. O bloco abaixo leva ao LLM
os horários REAIS já formatados, para ele citar sem inventar.
"""

import unicodedata
from collections.abc import Mapping
from datetime import date, datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from sdr.core.application.use_cases.conduzir_agendamento import ResultadoAgenda
from sdr.core.domain.agenda import (
    DIAS_SEMANA,
    AcaoAgenda,
    Agendamento,
    Aviso,
    Decisao,
    InterpretacaoAgenda,
    NegociacaoAgenda,
    Operacao,
    Periodo,
    PreferenciaHorario,
    Slot,
    TipoAgendamento,
    TipoDecisao,
    descrever_horario,
)


def _sem_acento(texto: str) -> str:
    return unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode().casefold()


# O LLM devolve "terca"/"sabado" mesmo com o enum acentuado (strict=False): o schema usa
# ASCII e a leitura aceita as duas grafias. Um dia descartado aqui virava "qualquer dia".
DIAS_ASCII = tuple(_sem_acento(d) for d in DIAS_SEMANA)
INDICE_DIA_SEMANA = {nome: i for i, nome in enumerate(DIAS_ASCII)}


def _inteiro(valor: object, minimo: int, maximo: int) -> int | None:
    if isinstance(valor, bool) or not isinstance(valor, int | float):
        return None
    inteiro = int(valor)
    return inteiro if minimo <= inteiro <= maximo else None


def ler_interpretacao(dados: Mapping[str, object], tipo: TipoAgendamento) -> InterpretacaoAgenda:
    """Converte a saída estruturada do LLM, descartando o que for inválido."""
    try:
        acao = AcaoAgenda(str(dados.get("acao")))
    except ValueError:
        acao = AcaoAgenda.NENHUMA
    bruta = dados.get("preferencia")
    pref: Mapping[str, object] = bruta if isinstance(bruta, Mapping) else {}
    dias = pref.get("dias_semana")
    data: date | None = None
    try:
        data = date.fromisoformat(str(pref["data"])) if pref.get("data") else None
    except ValueError:
        data = None
    try:
        periodo = Periodo(str(pref["periodo"])) if pref.get("periodo") else None
    except ValueError:
        periodo = None
    preferencia = PreferenciaHorario(
        dias_semana=tuple(
            sorted(
                {
                    INDICE_DIA_SEMANA[_sem_acento(d)]
                    for d in dias
                    if isinstance(d, str) and _sem_acento(d) in INDICE_DIA_SEMANA
                }
            )
            if isinstance(dias, list)
            else ()
        ),
        data=data,
        daqui_a_dias=_inteiro(pref.get("daqui_a_dias"), 0, 60),
        periodo=periodo,
        apos_hora=_inteiro(pref.get("apos_hora"), 0, 23),
        antes_hora=_inteiro(pref.get("antes_hora"), 1, 23),
        hora=_inteiro(pref.get("hora"), 0, 23),
    )
    modalidade = dados.get("modalidade")
    return InterpretacaoAgenda(
        acao=acao,
        preferencia=preferencia,
        opcao=_inteiro(dados.get("opcao"), 1, 20),
        modalidade=tipo.modalidade_valida(modalidade if isinstance(modalidade, str) else None),
    )


def schema_interpretacao(tipo: TipoAgendamento) -> dict[str, object]:
    inteiro_ou_nulo = {"type": ["integer", "null"]}
    return {
        "type": "object",
        "properties": {
            "acao": {"type": "string", "enum": [a.value for a in AcaoAgenda]},
            "opcao": inteiro_ou_nulo,
            "preferencia": {
                "type": "object",
                "properties": {
                    "dias_semana": {
                        "type": "array",
                        "items": {"type": "string", "enum": list(DIAS_ASCII)},
                    },
                    "data": {"type": ["string", "null"], "description": "AAAA-MM-DD"},
                    "daqui_a_dias": inteiro_ou_nulo,
                    "periodo": {
                        "type": ["string", "null"],
                        "enum": [*(p.value for p in Periodo), None],
                    },
                    "apos_hora": inteiro_ou_nulo,
                    "antes_hora": inteiro_ou_nulo,
                    "hora": inteiro_ou_nulo,
                },
                "additionalProperties": False,
            },
            "modalidade": {"type": ["string", "null"], "enum": [*tipo.modalidades, None]},
        },
        "required": ["acao", "opcao", "preferencia", "modalidade"],
        "additionalProperties": False,
    }


def prompt_interpretacao(
    modelo: str,
    tipo: TipoAgendamento,
    negociacao: NegociacaoAgenda,
    ativo: Agendamento | None,
    agora: datetime,
) -> str:
    """Prompt de sistema da interpretação; `agora` já no fuso da operação."""
    fuso = agora.tzinfo if isinstance(agora.tzinfo, ZoneInfo) else ZoneInfo("UTC")
    hoje = agora.date()

    def quando(slot: Slot) -> str:
        return descrever_horario(slot.inicio, fuso, hoje)

    ofertados = "\n".join(f"{i}. {quando(s)}" for i, s in enumerate(negociacao.ofertados, 1))
    proposta = "nenhuma"
    if (p := negociacao.proposta) is not None:
        if p.operacao is Operacao.CANCELAR:
            proposta = "cancelar o agendamento já marcado"
        elif p.slot is not None:
            proposta = f"{p.operacao.value} para {quando(p.slot)}"
    marcado = (
        f"{descrever_horario(ativo.inicio, fuso, hoje)} ({ativo.modalidade})" if ativo else "nenhum"
    )
    return modelo.format(
        tipo=tipo.rotulo,
        agora=f"{DIAS_SEMANA[agora.weekday()]}, {agora:%d/%m/%Y %H:%M}",
        fuso=fuso.key,
        ofertados=ofertados or "nenhum",
        proposta=proposta,
        agendamento=marcado,
        modalidades=", ".join(tipo.modalidades),
    )


AVISOS_AO_OFERECER = {
    Aviso.SEM_HORARIO_NA_PREFERENCIA: "Não há horário livre no que o lead pediu: diga isso "
    "com naturalidade e ofereça as alternativas abaixo.",
    Aviso.SLOT_TOMADO: "O horário que o lead escolheu acabou de ser ocupado: peça desculpas "
    "e ofereça as alternativas abaixo.",
    Aviso.PROPOSTA_RECUSADA: "O lead não quis o horário proposto: ofereça as alternativas abaixo.",
    Aviso.LEMBRETE: "Você já ofereceu estes horários: responda primeiro o que o lead disse e "
    "relembre as opções numa frase.",
}


class _Redacao:
    def __init__(self, resultado: ResultadoAgenda, fuso: ZoneInfo, hoje: date) -> None:
        self.r = resultado
        self.tipo = resultado.tipo
        self.fuso = fuso
        self.hoje = hoje

    def quem(self, responsavel_id: UUID) -> str:
        r = self.r.responsaveis.get(responsavel_id)
        return f" com {r.nome} ({r.titulo})" if r else ""

    def slot(self, slot: Slot) -> str:
        return descrever_horario(slot.inicio, self.fuso, self.hoje) + self.quem(slot.responsavel_id)

    def agendamento(self, ag: Agendamento) -> str:
        modalidade = self.tipo.modalidades.get(ag.modalidade, ag.modalidade)
        quando = descrever_horario(ag.inicio, self.fuso, self.hoje)
        return f"{self.tipo.rotulo}, {quando}{self.quem(ag.responsavel.id)}, {modalidade}"

    def modalidades(self) -> str:
        return " ou ".join(self.tipo.modalidades.values())

    def oferecer(self, decisao: Decisao) -> list[str]:
        linhas = [
            "NADA foi marcado, remarcado nem confirmado neste turno: NUNCA diga que "
            "confirmou, marcou ou remarcou — só ofereça os horários abaixo."
        ]
        if self.r.agendamento is not None:
            atual = self.agendamento(self.r.agendamento)
            linhas.append(f"Agendamento atual (o lead quer remarcar): {atual}.")
        if decisao.aviso in AVISOS_AO_OFERECER:
            linhas.append(AVISOS_AO_OFERECER[decisao.aviso])
        convite = (
            f"Convide o lead para a {self.tipo.rotulo} e ofereça"
            if self.r.agendamento is None and decisao.aviso is None
            else "Ofereça"
        )
        linhas.append(
            f"{convite} estes horários de forma natural, citando dia, data e hora exatamente "
            "como estão, e pergunte qual fica melhor:"
        )
        linhas += [f"{i}. {self.slot(s)}" for i, s in enumerate(decisao.opcoes, start=1)]
        if len(self.tipo.modalidades) > 1:
            linhas.append(
                f"Pode ser {self.modalidades()} — pergunte a preferência só depois que o lead "
                "escolher o horário."
            )
        return linhas

    def confirmar(self, decisao: Decisao) -> list[str]:
        p = decisao.proposta
        if p is None or p.operacao is Operacao.CANCELAR or p.slot is None:
            alvo = self.agendamento(self.r.agendamento) if self.r.agendamento else "o agendamento"
            return [
                f"O lead pediu para CANCELAR: {alvo}. Peça confirmação EXPLÍCITA do "
                "cancelamento. Ainda NÃO está cancelado: NUNCA diga que cancelou, mesmo que o "
                "lead pareça ter confirmado — pergunte de forma direta se pode cancelar."
            ]
        if decisao.aviso is Aviso.FALTA_MODALIDADE:
            return [
                f"Horário escolhido: {self.slot(p.slot)}. Pergunte se o lead prefere "
                f"{self.modalidades()}. Ainda NÃO está agendado."
            ]
        verbo = "remarcar para" if p.operacao is Operacao.REMARCAR else "agendar"
        modalidade = self.tipo.modalidades.get(p.modalidade or "", "")
        return [
            f"Peça confirmação EXPLÍCITA para {verbo}: {self.tipo.rotulo}, {self.slot(p.slot)}, "
            f"{modalidade}. Ainda NÃO está agendado: NUNCA diga que está marcado, mesmo que o "
            "lead pareça ter confirmado — pergunte de forma direta se pode confirmar."
        ]

    def feito(self, decisao: Decisao) -> list[str]:
        ag = self.r.agendamento
        if ag is None:
            return self.sem_disponibilidade()
        if decisao.tipo is TipoDecisao.CANCELADO:
            return [
                f"CANCELADO: {self.agendamento(ag)}. Confirme ao lead com gentileza e diga que "
                "é só chamar se quiser marcar outro horário."
            ]
        feito = "AGENDADO" if decisao.tipo is TipoDecisao.AGENDADO else "REMARCADO"
        return [
            f"{feito} com sucesso: {self.agendamento(ag)}. Confirme ao lead com esses dados e "
            f"diga que {ag.responsavel.nome} vai atendê-lo. Não faça mais perguntas sobre a "
            "agenda."
        ]

    def manter(self, decisao: Decisao) -> list[str]:
        linhas = []
        if decisao.aviso is Aviso.SEM_AGENDAMENTO:
            linhas.append(
                "O lead pediu para cancelar, mas não há agendamento ativo: diga isso com "
                "naturalidade."
            )
        if self.r.agendamento is not None:
            linhas.append(
                f"Já está agendado: {self.agendamento(self.r.agendamento)}. Responda ao lead; "
                "mencione o agendamento só se for pertinente (ele pode pedir para remarcar ou "
                "cancelar)."
            )
        return linhas

    @staticmethod
    def sem_disponibilidade(_: Decisao | None = None) -> list[str]:
        return [
            "Não há horários livres nos próximos dias: diga que a equipe vai entrar em contato "
            "para combinar. NÃO invente horário."
        ]


def bloco_agenda(resultado: ResultadoAgenda, fuso: ZoneInfo, hoje: date) -> str:
    """Instruções internas para a resposta do turno, conforme a decisão da agenda."""
    redacao = _Redacao(resultado, fuso, hoje)
    por_decisao = {
        TipoDecisao.OFERECER: redacao.oferecer,
        TipoDecisao.PEDIR_CONFIRMACAO: redacao.confirmar,
        TipoDecisao.AGENDADO: redacao.feito,
        TipoDecisao.REMARCADO: redacao.feito,
        TipoDecisao.CANCELADO: redacao.feito,
        TipoDecisao.MANTER: redacao.manter,
    }
    decisao = resultado.decisao
    gerar = por_decisao.get(decisao.tipo, redacao.sem_disponibilidade)
    linhas = [
        "[Agenda — uso interno, nunca mostre isto ao lead]",
        f"Atendimento a combinar: {resultado.tipo.rotulo}.",
        *gerar(decisao),
    ]
    return "\n".join(linhas)
