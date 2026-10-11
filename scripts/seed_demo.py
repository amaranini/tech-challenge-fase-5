"""Dados sintéticos para o dashboard: ~40 leads nos últimos 14 dias, marcados como demo.

Cria leads com conversa, ficha, score, eventos (lead_eventos), agendamentos, handoffs,
follow-ups, reengajamentos e opt-outs — coerentes entre si (funil, tempos, canais) — e
todos com `leads.demo = true`, para sair num comando só.

Uso (do host, com o compose no ar e o `make seed` já rodado — usa os responsáveis da
agenda mock):
    uv run python scripts/seed_demo.py              # recria os dados de demo
    uv run python scripts/seed_demo.py --remover    # apaga tudo o que é demo
    uv run python scripts/seed_demo.py --total 60 --semente 7
"""

import argparse
import asyncio
import random
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select

from sdr.config.settings import obter_settings
from sdr.core.adapters.outbound.persistence.database import criar_engine, criar_fabrica_sessao
from sdr.core.adapters.outbound.persistence.modelos import (
    AgendamentoModel,
    ConversaModel,
    FollowUpModel,
    LeadEventoModel,
    LeadModel,
    MensagemModel,
    ResponsavelModel,
    SlotAgendaModel,
)
from sdr.core.domain.eventos import TipoEvento as T

FUSO = ZoneInfo("America/Sao_Paulo")
NOMES = (
    "Ana Souza", "Bruno Lima", "Carla Mendes", "Diego Rocha", "Eduarda Alves", "Felipe Nunes",
    "Gabriela Dias", "Henrique Costa", "Isabela Martins", "João Pedro Silva", "Karina Lopes",
    "Lucas Ferreira", "Mariana Ribeiro", "Nicolas Araújo", "Olívia Castro", "Paulo Teixeira",
    "Rafaela Gomes", "Samuel Barbosa", "Tatiane Moreira", "Vinícius Cardoso",
)  # fmt: skip
REGIOES = ("zona sul", "Vila Mariana", "Moema", "Pinheiros", "zona oeste", "Perdizes", "Saúde")


@dataclass
class Lead:
    id: UUID
    canal: str
    remetente: str
    nome: str | None
    criado_em: datetime
    etapa: int  # 0 novo, 1 com intenção, 2 qualificado, 3 agendado
    intencao: str | None = None
    ficha: dict[str, Any] = field(default_factory=dict)
    score: int | None = None
    eventos: list[LeadEventoModel] = field(default_factory=list)
    mensagens: list[tuple[str, str, datetime]] = field(default_factory=list)

    def evento(self, tipo_evento: T, quando: datetime, /, **payload: Any) -> None:
        self.eventos.append(
            LeadEventoModel(
                id=uuid4(),
                lead_id=self.id,
                tipo=tipo_evento.value,
                payload=payload,
                ocorrido_em=quando,
            )
        )


def _ficha(r: random.Random, intencao: str, completa: bool) -> dict[str, Any]:
    if intencao == "compra":
        ficha = {
            "regiao": r.choice(REGIOES),
            "preco_max": r.choice([450_000, 600_000, 800_000, 1_200_000]),
            "quartos": r.choice([1, 2, 2, 3]),
            "urgencia": r.choice(["imediata", "ate_3_meses", "3_a_6_meses"]),
            "forma_pagamento": r.choice(
                [["financiamento"], ["financiamento", "fgts"], ["a_vista"]]
            ),
        }
    elif intencao == "aluguel":
        ficha = {
            "regiao": r.choice(REGIOES),
            "aluguel_max": r.choice([2_500, 3_500, 5_000]),
            "quartos": r.choice([1, 2, 3]),
            "prazo_mudanca": r.choice(["imediato", "ate_1_mes", "ate_3_meses"]),
            "tipo_garantia": r.choice(["fiador", "seguro_fianca", "caucao"]),
        }
    else:
        ficha = {
            "ticket": r.choice([300_000, 500_000, 1_000_000]),
            "objetivo": r.choice(["renda", "valorizacao", "ambos"]),
            "prazo_investimento": r.choice(["curto", "medio", "longo"]),
            "experiencia_previa": r.choice([True, False]),
        }
    if completa:
        return ficha
    return dict(list(ficha.items())[: r.randint(1, len(ficha) - 2)])


def _classificacao(score: int) -> str:
    return "quente" if score >= 70 else "morno" if score >= 40 else "frio"


def _horario_util(r: random.Random, dia: datetime) -> datetime:
    local = dia.astimezone(FUSO).replace(hour=r.randint(8, 21), minute=r.randint(0, 59))
    return local.astimezone(UTC)


FALAS = {
    "compra": ("Oi! Quero comprar um apartamento", "Ótimo! Em qual região você procura?"),
    "aluguel": ("Boa tarde, procuro apê para alugar", "Claro! Qual região e até quanto?"),
    "investimento": ("Quero investir em imóveis", "Legal! Quanto pensa em investir?"),
    None: ("Oi, tudo bem?", "Oi! Você quer comprar, alugar ou investir?"),
}  # fmt: skip


def gerar(r: random.Random, total: int, agora: datetime) -> list[Lead]:
    leads = []
    for i in range(total):
        canal = "whatsapp" if r.random() < 0.55 else "web"
        remetente = (
            f"+55{r.choice([11, 11, 11, 21, 31, 41])}9{r.randint(10_000_000, 99_999_999)}"
            if canal == "whatsapp"
            else f"demo-{uuid4().hex[:6]}"
        )
        criado = _horario_util(r, agora - timedelta(days=r.uniform(0, 13.8)))
        criado = min(criado, agora - timedelta(minutes=30))
        etapa = r.choices([0, 1, 2, 3], weights=[15, 30, 25, 30])[0]
        lead = Lead(
            uuid4(), canal, remetente, NOMES[i % len(NOMES)] if canal == "whatsapp" else None,
            criado, etapa,
        )  # fmt: skip
        lead.evento(T.LEAD_CRIADO, criado, canal=canal)
        t = criado
        resposta_s = r.uniform(5, 30)  # debounce + LLM
        if etapa >= 1:
            lead.intencao = r.choices(["compra", "aluguel", "investimento"], weights=[5, 3, 2])[0]
        fala, resposta = FALAS[lead.intencao]
        lead.mensagens += [
            ("lead", fala, t),
            ("assistente", resposta, t + timedelta(seconds=resposta_s)),
        ]
        if etapa >= 1:
            t += timedelta(minutes=r.uniform(1, 4))
            lead.evento(T.INTENCAO_IDENTIFICADA, t, intencao=lead.intencao, confianca=0.92)
            lead.ficha = _ficha(r, lead.intencao, completa=etapa >= 2)
            for campo, valor in lead.ficha.items():
                t += timedelta(minutes=r.uniform(0.5, 3))
                lead.evento(T.CAMPO_PREENCHIDO, t, campo=campo, valor=valor)
                depois = t + timedelta(seconds=r.uniform(5, 25))
                lead.mensagens += [
                    ("lead", f"{campo.replace('_', ' ')}: {valor}", t),
                    ("assistente", "Anotado! Vou te mostrar opções que combinam.", depois),
                ]
            lead.score = r.randint(55, 95) if etapa >= 2 else r.randint(10, 55)
            lead.evento(
                T.SCORE_ALTERADO, t, de=None, para=lead.score,
                classificacao_para=_classificacao(lead.score),
            )  # fmt: skip
        if etapa >= 2:
            t += timedelta(minutes=r.uniform(1, 30))
            acao = (
                "encaminhar_especialista" if lead.intencao == "investimento" else "agendar_visita"
            )
            lead.evento(T.LEAD_QUALIFICADO, t, proxima_acao=acao, score=lead.score)
        leads.append(lead)
    return leads


async def _responsaveis(sessao: Any) -> dict[str, ResponsavelModel]:
    por_especialidade: dict[str, ResponsavelModel] = {}
    for resp in (await sessao.scalars(select(ResponsavelModel))).all():
        for esp in resp.especialidades:
            por_especialidade[esp] = resp
    return por_especialidade


def _responsavel(
    lead: Lead, por_esp: dict[str, ResponsavelModel], r: random.Random
) -> ResponsavelModel:
    if lead.intencao == "investimento":
        return por_esp["investimentos"]
    if lead.intencao == "aluguel":
        return por_esp["locacao"]
    return por_esp[r.choice(["compra:sul", "compra:oeste"])]


async def remover(sessoes: Any) -> int:
    async with sessoes.begin() as sessao:
        demo = select(LeadModel.id).where(LeadModel.demo.is_(True))
        agendamentos = (
            await sessao.scalars(
                select(AgendamentoModel.id).where(AgendamentoModel.lead_id.in_(demo))
            )
        ).all()
        slots = (
            await sessao.scalars(
                select(AgendamentoModel.slot_id).where(AgendamentoModel.id.in_(agendamentos))
            )
        ).all()
        await sessao.execute(delete(AgendamentoModel).where(AgendamentoModel.id.in_(agendamentos)))
        await sessao.execute(delete(SlotAgendaModel).where(SlotAgendaModel.id.in_(slots)))
        resultado = await sessao.execute(delete(LeadModel).where(LeadModel.demo.is_(True)))
    return int(resultado.rowcount)  # type: ignore[attr-defined]


async def criar(sessoes: Any, total: int, semente: int, agora: datetime) -> dict[str, int]:
    r = random.Random(semente)
    leads = gerar(r, total, agora)
    resumo = {"leads": len(leads), "agendamentos": 0, "handoffs": 0, "followups": 0}
    async with sessoes.begin() as sessao:
        por_esp = await _responsaveis(sessao)
        if not por_esp:
            raise SystemExit("Sem responsáveis na agenda: rode `make seed` antes.")
        ocupados = {
            (resp, inicio)
            for resp, inicio in await sessao.execute(
                select(SlotAgendaModel.responsavel_id, SlotAgendaModel.inicio)
            )
        }
        sem_agenda = [ld for ld in leads if ld.etapa < 3]
        handoff = set(map(id, r.sample(sem_agenda, k=min(7, len(sem_agenda)))))
        nutrir = {
            id(ld)
            for ld in sem_agenda
            if id(ld) not in handoff and ld.criado_em < agora - timedelta(days=1)
        }
        for lead in leads:
            estado, na_fila, atendimento = "atendimento_ia", None, {}
            pendentes: list[FollowUpModel] = []  # depois do lead (chave estrangeira)
            opt_out_em = None
            ultimo = max(e.ocorrido_em for e in lead.eventos)
            if lead.etapa == 3:
                resp = _responsavel(lead, por_esp, r)
                inicio = _horario_util(r, lead.criado_em + timedelta(days=r.uniform(1, 6))).replace(
                    minute=30
                )
                inicio = inicio.replace(second=0, microsecond=0)
                while (resp.id, inicio) in ocupados:  # um horário por responsável
                    inicio += timedelta(hours=1)
                ocupados.add((resp.id, inicio))
                status = "cancelado" if r.random() < 0.1 else "ativo"
                ag_id, slot_id = uuid4(), uuid4()
                tipo = (
                    "reuniao_especialista" if lead.intencao == "investimento" else "visita_imovel"
                )
                modalidade = (
                    r.choice(["online", "escritorio"])
                    if tipo == "reuniao_especialista"
                    else "presencial"
                )
                sessao.add(
                    SlotAgendaModel(
                        id=slot_id,
                        responsavel_id=resp.id,
                        inicio=inicio,
                        fim=inicio + timedelta(hours=1),
                        agendamento_id=ag_id,
                    )
                )
                await sessao.flush()
                quando = ultimo + timedelta(minutes=r.uniform(2, 15))
                sessao.add(
                    LeadModel(
                        id=lead.id,
                        canal=lead.canal,
                        remetente_id=lead.remetente,
                        criado_em=lead.criado_em,
                        demo=True,
                    )
                )
                await sessao.flush()
                sessao.add(
                    AgendamentoModel(
                        id=ag_id, lead_id=lead.id, responsavel_id=resp.id, slot_id=slot_id,
                        inicio=inicio, fim=inicio + timedelta(hours=1), tipo=tipo,
                        modalidade=modalidade, status=status, itens=[],
                        chave_idempotencia=f"demo-{ag_id}", criado_em=quando,
                    )
                )  # fmt: skip
                lead.evento(
                    T.AGENDAMENTO_CRIADO, quando, agendamento_id=str(ag_id), tipo=tipo,
                    modalidade=modalidade, inicio=inicio.isoformat(), responsavel_nome=resp.nome,
                    responsavel_titulo=resp.titulo,
                )  # fmt: skip
                if status == "cancelado":
                    lead.evento(
                        T.AGENDAMENTO_CANCELADO,
                        quando + timedelta(hours=r.uniform(2, 20)),
                        agendamento_id=str(ag_id),
                        inicio=inicio.isoformat(),
                    )
                resumo["agendamentos"] += 1
            elif id(lead) in handoff:
                resumo["handoffs"] += 1
                pediu = ultimo + timedelta(minutes=r.uniform(1, 10))
                lead.evento(T.HANDOFF_SOLICITADO, pediu, motivo="pedido_explicito")
                fila = pediu + timedelta(seconds=40)
                lead.evento(T.HANDOFF_CONFIRMADO, fila)
                destino = r.choice(["aguardando_humano", "atendimento_humano", "devolvido"])
                if lead.criado_em > agora - timedelta(hours=6) or destino == "aguardando_humano":
                    estado, na_fila = (
                        "aguardando_humano",
                        agora - timedelta(minutes=r.uniform(4, 45)),
                    )
                    lead.eventos[-1].ocorrido_em = na_fila
                    atendimento = {"desde": na_fila.isoformat(), "motivo": "pedido_explicito"}
                    if agora - na_fila > timedelta(minutes=15):
                        lead.evento(
                            T.HANDOFF_SLA_EXCEDIDO,
                            na_fila + timedelta(minutes=15),
                            na_fila_desde=na_fila.isoformat(),
                            espera_util_minutos=15,
                            sla_minutos=15,
                        )
                else:
                    quem = r.choice(["Rafael Souza", "Camila Ito", "Bruno Lima"])
                    assumiu = fila + timedelta(minutes=r.uniform(2, 14))
                    lead.evento(
                        T.ATENDIMENTO_HUMANO_INICIADO,
                        assumiu,
                        responsavel=quem,
                        espera_segundos=int((assumiu - fila).total_seconds()),
                    )
                    lead.mensagens.append(
                        (
                            "responsavel",
                            f"Oi! Aqui é {quem.split()[0]}, vou te ajudar.",
                            assumiu + timedelta(seconds=30),
                        )
                    )
                    if destino == "devolvido":
                        lead.evento(
                            T.ATENDIMENTO_HUMANO_ENCERRADO,
                            assumiu + timedelta(minutes=20),
                            responsavel=quem,
                        )
                    else:
                        estado = "atendimento_humano"
                        atendimento = {
                            "desde": assumiu.isoformat(),
                            "motivo": "pedido_explicito",
                            "responsavel": quem,
                        }
            elif id(lead) in nutrir:
                resumo["followups"] += 1
                etapas = r.choice([1, 1, 2, 3])
                quando = ultimo
                for etapa in range(1, etapas + 1):
                    quando += timedelta(days=(1 if etapa == 1 else 2 if etapa == 2 else 4))
                    if quando > agora:
                        break
                    lead.evento(
                        T.FOLLOWUP_ENVIADO,
                        quando,
                        tipo="retomada",
                        etapa=etapa,
                        requer_template=etapa > 1,
                    )
                    lead.mensagens.append(
                        (
                            "assistente",
                            "Oi! Ainda está procurando imóvel? Separei novidades.",
                            quando,
                        )
                    )
                desfecho = r.choices(
                    ["nada", "reengajou", "opt_out", "encerrado"], weights=[4, 3, 2, 1]
                )[0]
                if quando > agora:
                    desfecho = "nada"
                if desfecho == "reengajou":
                    voltou = quando + timedelta(hours=r.uniform(1, 12))
                    if voltou < agora:
                        lead.mensagens.append(
                            ("lead", "Oi, voltei! Ainda tenho interesse.", voltou)
                        )
                        lead.evento(T.LEAD_REENGAJADO, voltou, etapa=etapas)
                elif desfecho == "opt_out":
                    opt_out_em = min(quando + timedelta(hours=r.uniform(1, 8)), agora)
                    lead.mensagens.append(
                        ("lead", "Por favor, não me mande mais mensagens.", opt_out_em)
                    )
                    lead.evento(T.LEAD_OPT_OUT, opt_out_em, fala="não me mande mais mensagens")
                elif desfecho == "encerrado" and etapas == 3:
                    lead.evento(T.LEAD_ENCERRADO_INATIVIDADE, quando, etapa=3)
                elif desfecho == "nada" and etapas < 3:
                    pendentes.append(
                        FollowUpModel(
                            id=uuid4(),
                            lead_id=lead.id,
                            tipo="retomada",
                            etapa=etapas + 1,
                            situacao="qualificacao",
                            executar_em=agora + timedelta(hours=r.uniform(2, 48)),
                            status="pendente",
                            criado_em=min(quando, agora),
                        )
                    )
            q_em = next(
                (e.ocorrido_em for e in lead.eventos if e.tipo == T.LEAD_QUALIFICADO.value), None
            )
            acao = next(
                (
                    e.payload["proxima_acao"]
                    for e in lead.eventos
                    if e.tipo == T.LEAD_QUALIFICADO.value
                ),
                None,
            )
            existente = await sessao.get(LeadModel, lead.id)
            valores = {
                "canal": lead.canal, "remetente_id": lead.remetente, "nome": lead.nome,
                "ficha_qualificacao": {lead.intencao: lead.ficha} if lead.intencao else {},
                "intencao_atual": lead.intencao, "score": lead.score,
                "classificacao": _classificacao(lead.score) if lead.score is not None else None,
                "score_motivos": [f"+{lead.score} (dado de demonstração)"] if lead.score else [],
                "proxima_acao": acao, "qualificado_em": q_em, "atendimento_estado": estado,
                "na_fila_desde": na_fila, "atendimento": atendimento, "opt_out_em": opt_out_em,
                "criado_em": lead.criado_em, "demo": True,
            }  # fmt: skip
            if existente is None:
                sessao.add(LeadModel(id=lead.id, **valores))
            else:
                for chave, valor in valores.items():
                    setattr(existente, chave, valor)
            conversa = ConversaModel(
                id=uuid4(), lead_id=lead.id, canal=lead.canal, status="aberta",
                iniciada_em=lead.criado_em, atualizada_em=max(m[2] for m in lead.mensagens),
            )  # fmt: skip
            await sessao.flush()
            sessao.add(conversa)
            await sessao.flush()
            for papel, texto, quando in sorted(lead.mensagens, key=lambda m: m[2]):
                sessao.add(
                    MensagemModel(
                        id=uuid4(), conversa_id=conversa.id, papel=papel, texto=texto,
                        metadados={"demo": True}, criada_em=quando,
                        status="processada" if papel == "lead" else "enviada",
                    )
                )  # fmt: skip
            sessao.add_all(lead.eventos)
            sessao.add_all(pendentes)
    return resumo


async def principal(args: argparse.Namespace) -> None:
    engine = criar_engine(obter_settings().database_url)
    sessoes = criar_fabrica_sessao(engine)
    try:
        removidos = await remover(sessoes)
        print(f"{removidos} lead(s) de demo removido(s)")
        if not args.remover:
            criados = await criar(sessoes, args.total, args.semente, datetime.now(UTC))
            print(
                f"Criados: {criados['leads']} leads, {criados['agendamentos']} agendamentos, "
                f"{criados['handoffs']} handoffs, {criados['followups']} com follow-up"
            )
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--remover", action="store_true", help="só apaga os dados de demo")
    parser.add_argument("--total", type=int, default=40)
    parser.add_argument("--semente", type=int, default=42)
    asyncio.run(principal(parser.parse_args()))


if __name__ == "__main__":
    main()
