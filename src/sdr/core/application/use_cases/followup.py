"""Follow-up automático: programar (na conversa) e executar (no worker).

- `ProgramarFollowUps` agenda e cancela: depois de cada resposta do assistente, a cadência
  recomeça (a 1ª etapa conta da última resposta); quando o lead fala, os pendentes caem
  (e, se ele respondia a um follow-up, vira LeadReengajado); opt-out encerra tudo; assina
  eventos para o lembrete do agendamento e o SLA da fila do humano.
- `ExecutarFollowUps` é o que o worker chama em laço: reserva os vencidos (SKIP LOCKED),
  confere a elegibilidade e envia pelo canal — ou adia/cancela, dizendo por quê.

As regras (cadência, elegibilidade, SLA) estão no domínio; aqui só se orquestra.
"""

import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from uuid import UUID

from sdr.core.application.ports.agenda import AgendaPort
from sdr.core.application.ports.canal import CanalMensagemPort
from sdr.core.application.ports.catalogo import CatalogoPort
from sdr.core.application.ports.followup import (
    FollowUpRepository,
    MensagemAtiva,
    PedidoMensagemAtiva,
    RedatorMensagemAtivaPort,
)
from sdr.core.application.ports.relogio import RelogioPort
from sdr.core.application.ports.repositorios import (
    ConversaRepository,
    LeadEventoRepository,
    LeadRepository,
)
from sdr.core.domain.agenda import Agendamento
from sdr.core.domain.agente import Persona
from sdr.core.domain.atendimento import EstadoAtendimento, HorarioAtendimento
from sdr.core.domain.catalogo import ConsultaCatalogo, ItemCatalogo
from sdr.core.domain.conversa import Canal, Conversa, Lead, Mensagem, Papel, StatusConversa
from sdr.core.domain.eventos import EventoLead, TipoEvento
from sdr.core.domain.followup import (
    Cadencia,
    ContextoElegibilidade,
    FollowUp,
    ModeloLembrete,
    MotivoInelegivel,
    SituacaoLead,
    StatusFollowUp,
    TipoFollowUp,
    UnidadeTempo,
    motivo_inelegivel,
    proxima_checagem_sla,
    requer_template,
    situacao_do_lead,
    tempo_util,
)
from sdr.core.domain.qualificacao import Ficha
from sdr.core.domain.resumo import itens_citados

logger = logging.getLogger(__name__)

# Monta a busca de "algo novo e compatível" a partir da ficha (vocabulário da vertical).
ConsultaParaFicha = Callable[[str, Ficha], ConsultaCatalogo | None]

LIMITE_HISTORICO = 40


@dataclass(frozen=True)
class ConfigFollowUp:
    cadencias: Mapping[SituacaoLead, Cadencia]
    unidade: UnidadeTempo = UnidadeTempo.DIAS
    horario: HorarioAtendimento | None = None
    respeitar_horario: bool = True
    sla_handoff: timedelta = timedelta(minutes=15)
    lembrete: ModeloLembrete | None = None  # da vertical; None = sem lembretes
    lembrete_antes: timedelta = timedelta(hours=24)
    janela_conversa: timedelta = timedelta(hours=24)  # depois disso: template (WhatsApp)


def _evento(
    lead_id: UUID, tipo_evento: TipoEvento, momento: datetime, /, **payload: object
) -> EventoLead:
    return EventoLead(lead_id, tipo_evento, momento, payload)


class OptOutError(RuntimeError):
    """O lead pediu para não receber mensagens ativas."""


class ProgramarFollowUps:
    def __init__(
        self,
        followups: FollowUpRepository,
        leads: LeadRepository,
        conversas: ConversaRepository,
        *,
        eventos: LeadEventoRepository,
        agenda: AgendaPort,
        relogio: RelogioPort,
        config: ConfigFollowUp,
    ) -> None:
        self._followups = followups
        self._leads = leads
        self._conversas = conversas
        self._eventos = eventos
        self._agenda = agenda
        self._relogio = relogio
        self._config = config

    # ------------------------------------------------------------------ conversa
    async def apos_resposta(self, lead: Lead) -> list[EventoLead]:
        """O assistente acabou de responder: a cadência recomeça desta resposta."""
        await self._followups.cancelar_pendentes(lead.id, [TipoFollowUp.RETOMADA], "nova_resposta")
        if lead.opt_out_em or lead.atendimento_atual.estado is not EstadoAtendimento.ATENDIMENTO_IA:
            return []
        return await self.agendar_etapa(lead, situacao_do_lead(lead.qualificacao), 1, ignorar=False)

    async def agendar_etapa(
        self, lead: Lead, situacao: SituacaoLead, numero: int, *, ignorar: bool, ja: bool = False
    ) -> list[EventoLead]:
        cadencia = self._config.cadencias.get(situacao)
        etapa = cadencia.etapa(numero) if cadencia else None
        if etapa is None:
            return []
        agora = self._relogio.agora()
        quando = agora if ja else agora + self._config.unidade.duracao(etapa.apos)
        followup = FollowUp.novo(
            lead.id,
            TipoFollowUp.RETOMADA,
            quando,
            agora,
            etapa=numero,
            situacao=situacao,
            ignorar_horario=ignorar,
        )
        await self._followups.agendar(followup)
        evento = _evento(
            lead.id,
            TipoEvento.FOLLOWUP_AGENDADO,
            agora,
            followup_id=str(followup.id),
            tipo=followup.tipo.value,
            etapa=numero,
            situacao=situacao.value,
            executar_em=quando.isoformat(),
        )
        await self._eventos.registrar([evento])
        return [evento]

    async def lead_respondeu(self, lead: Lead, conversa: Conversa) -> list[EventoLead]:
        """Chamado ao RECEBER uma mensagem (antes de gravá-la): cancela os pendentes e, se a
        última mensagem era um follow-up, registra o reengajamento."""
        agora = self._relogio.agora()
        await self._followups.cancelar_pendentes(
            lead.id, [TipoFollowUp.RETOMADA], MotivoInelegivel.LEAD_RESPONDEU.value
        )
        ultimas = await self._conversas.ultimas_mensagens(conversa.id, 1)
        anterior = ultimas[-1] if ultimas else None
        followup = anterior.metadados.get("followup") if anterior else None
        if not isinstance(followup, Mapping) or followup.get("tipo") != TipoFollowUp.RETOMADA:
            return []
        evento = _evento(lead.id, TipoEvento.LEAD_REENGAJADO, agora, etapa=followup.get("etapa"))
        await self._eventos.registrar([evento])
        return [evento]

    async def registrar_opt_out(self, lead: Lead, texto: str) -> list[EventoLead]:
        agora = self._relogio.agora()
        await self._leads.registrar_opt_out(lead.id, agora)
        cancelados = await self._followups.cancelar_pendentes(
            lead.id, list(TipoFollowUp), MotivoInelegivel.OPT_OUT.value
        )
        evento = _evento(
            lead.id,
            TipoEvento.LEAD_OPT_OUT,
            agora,
            fala=texto[:200],
            followups_cancelados=cancelados,
        )
        await self._eventos.registrar([evento])
        return [evento]

    # ------------------------------------------------------------------ eventos (fora do turno)
    async def ao_publicar(self, eventos: Sequence[EventoLead]) -> None:
        for evento in eventos:
            lead = await self._leads.obter(evento.lead_id)
            if lead is None:
                continue
            match evento.tipo:
                case TipoEvento.AGENDAMENTO_CRIADO | TipoEvento.AGENDAMENTO_REMARCADO:
                    await self._programar_lembrete(lead, evento)
                case TipoEvento.AGENDAMENTO_CANCELADO:
                    await self._followups.cancelar_pendentes(
                        lead.id, [TipoFollowUp.LEMBRETE_AGENDAMENTO], "agendamento_cancelado"
                    )
                case TipoEvento.HANDOFF_CONFIRMADO:
                    await self._programar_sla(lead)
                case TipoEvento.ATENDIMENTO_HUMANO_INICIADO | TipoEvento.RETORNO_IA_CONFIRMADO:
                    await self._followups.cancelar_pendentes(
                        lead.id, [TipoFollowUp.SLA_HANDOFF], "saiu_da_fila"
                    )

    async def _programar_lembrete(self, lead: Lead, evento: EventoLead) -> None:
        await self._followups.cancelar_pendentes(
            lead.id, [TipoFollowUp.LEMBRETE_AGENDAMENTO], "agendamento_alterado"
        )
        if self._config.lembrete is None:
            return  # a vertical não definiu lembrete
        inicio = datetime.fromisoformat(str(evento.payload["inicio"]))
        quando = inicio - self._config.lembrete_antes
        agora = self._relogio.agora()
        if quando <= agora:
            return  # marcado com menos antecedência que o lembrete
        await self._followups.agendar(
            FollowUp.novo(
                lead.id,
                TipoFollowUp.LEMBRETE_AGENDAMENTO,
                quando,
                agora,
                referencia=str(evento.payload["agendamento_id"]),
                ignorar_horario=True,  # o lembrete tem hora certa
            )
        )

    async def _programar_sla(self, lead: Lead) -> None:
        horario = self._config.horario
        desde = lead.atendimento_atual.na_fila_desde
        if horario is None or desde is None:
            return
        agora = self._relogio.agora()
        quando = proxima_checagem_sla(desde, self._config.sla_handoff, agora, horario) or agora
        await self._followups.agendar(
            FollowUp.novo(
                lead.id,
                TipoFollowUp.SLA_HANDOFF,
                quando,
                agora,
                referencia=desde.isoformat(),
                ignorar_horario=True,
            )
        )

    # ------------------------------------------------------------------ demo
    async def simular_inatividade(self, canal: Canal, remetente_id: str) -> FollowUp | None:
        """Demo: "o lead sumiu" — a próxima retomada sai já (sem esperar o expediente)."""
        lead = await self._leads.obter_por_remetente(canal, remetente_id)
        if lead is None:
            raise LookupError(remetente_id)
        if lead.opt_out_em is not None:
            raise OptOutError(remetente_id)
        pendentes = [
            f for f in await self._followups.pendentes(lead.id) if f.tipo is TipoFollowUp.RETOMADA
        ]
        agora = self._relogio.agora()
        if pendentes:
            antecipado = replace(pendentes[0], executar_em=agora, ignorar_horario=True)
            await self._followups.reagendar(antecipado)
            return antecipado
        situacao = situacao_do_lead(lead.qualificacao)
        await self.agendar_etapa(lead, situacao, 1, ignorar=True, ja=True)
        novos = await self._followups.pendentes(lead.id)
        return next((f for f in novos if f.tipo is TipoFollowUp.RETOMADA), None)


class ExecutarFollowUps:
    def __init__(
        self,
        followups: FollowUpRepository,
        programar: ProgramarFollowUps,
        leads: LeadRepository,
        *,
        conversas: ConversaRepository,
        eventos: LeadEventoRepository,
        agenda: AgendaPort,
        catalogo: CatalogoPort,
        redator: RedatorMensagemAtivaPort,
        canais: Mapping[Canal, CanalMensagemPort],
        relogio: RelogioPort,
        persona: Persona,
        config: ConfigFollowUp,
        consulta_para_ficha: ConsultaParaFicha | None = None,
    ) -> None:
        self._followups = followups
        self._programar = programar
        self._leads = leads
        self._conversas = conversas
        self._eventos = eventos
        self._agenda = agenda
        self._catalogo = catalogo
        self._redator = redator
        self._canais = canais
        self._relogio = relogio
        self._persona = persona
        self._config = config
        self._consulta = consulta_para_ficha

    async def executar(self, limite: int = 20) -> int:
        """Processa os vencidos; devolve quantos foram tratados (enviados, adiados ou não)."""
        lote = await self._followups.reservar_vencidos(self._relogio.agora(), limite)
        for followup in lote:
            try:
                await self._processar(followup)
            except Exception:
                logger.exception("Follow-up %s falhou; volta para a fila", followup.id)
                await self._followups.reagendar(
                    followup.adiar(self._relogio.agora() + timedelta(minutes=5), "erro")
                )
        return len(lote)

    async def _processar(self, followup: FollowUp) -> None:
        lead = await self._leads.obter(followup.lead_id)
        conversa = await self._conversas.obter_aberta(followup.lead_id) if lead else None
        if lead is None:
            await self._followups.concluir(
                followup.id, StatusFollowUp.CANCELADO, "lead_inexistente"
            )
            return
        if followup.tipo is TipoFollowUp.SLA_HANDOFF:
            await self._checar_sla(followup, lead)
            return

        historico = (
            await self._conversas.ultimas_mensagens(conversa.id, LIMITE_HISTORICO)
            if conversa
            else []
        )
        ultima_do_lead = max(
            (m.criada_em for m in historico if m.papel is Papel.LEAD), default=None
        )
        agora = self._relogio.agora()
        agendamento = await self._agenda.agendamento_ativo(lead.id, agora)
        ctx = ContextoElegibilidade(
            atendimento=lead.atendimento_atual,
            opt_out=lead.opt_out_em is not None,
            conversa_aberta=conversa is not None and conversa.status is StatusConversa.ABERTA,
            tem_agendamento_futuro=agendamento is not None,
            lead_falou_por_ultimo_em=ultima_do_lead,
        )
        horario = self._config.horario
        motivo = (
            motivo_inelegivel(
                followup, ctx, agora, horario, respeitar_horario=self._config.respeitar_horario
            )
            if horario
            else None
        )
        if motivo is MotivoInelegivel.FORA_DO_HORARIO and horario is not None:
            await self._followups.reagendar(
                followup.adiar(horario.proxima_abertura(agora), motivo.value)
            )
            return
        if motivo is not None:
            await self._followups.concluir(followup.id, StatusFollowUp.CANCELADO, motivo.value)
            return
        assert conversa is not None
        if followup.tipo is TipoFollowUp.LEMBRETE_AGENDAMENTO:
            await self._lembrar(
                followup,
                lead,
                conversa,
                historico,
                agendamento=agendamento,
                ultima_do_lead=ultima_do_lead,
            )
        else:
            await self._retomar(followup, lead, conversa, historico, ultima_do_lead)

    # ------------------------------------------------------------------ retomada
    async def _retomar(
        self,
        followup: FollowUp,
        lead: Lead,
        conversa: Conversa,
        historico: Sequence[Mensagem],
        ultima_do_lead: datetime | None,
    ) -> None:
        situacao = followup.situacao or situacao_do_lead(lead.qualificacao)
        cadencia = self._config.cadencias.get(situacao)
        etapa = cadencia.etapa(followup.etapa) if cadencia else None
        if etapa is None:
            await self._followups.concluir(followup.id, StatusFollowUp.CANCELADO, "sem_cadencia")
            return
        itens = [] if etapa.encerramento else await self._item_novo(lead, historico)
        texto, resposta = await self._redigir_sem_inventar(
            PedidoMensagemAtiva(
                lead, historico, etapa.objetivo, etapa.encerramento, itens_novos=itens
            ),
            itens,
            historico,
        )
        agora = self._relogio.agora()
        template = requer_template(ultima_do_lead, agora, self._config.janela_conversa)
        await self._enviar(
            lead,
            conversa,
            texto,
            {
                "followup": {
                    "tipo": followup.tipo.value,
                    "etapa": followup.etapa,
                    "encerramento": etapa.encerramento,
                },
                "requer_template": template,
                "template": etapa.template,
                "itens_citados": [
                    {"id": i.id, "titulo": i.titulo, "resumo": i.resumo}
                    for i in itens
                    if i.id in texto
                ],
                "prompt_versao": self._persona.versao_prompt,
                "modelo": resposta.modelo,
                "tokens": {"entrada": resposta.tokens_entrada, "saida": resposta.tokens_saida},
            },
            template=etapa.template if template else None,
        )
        await self._followups.concluir(followup.id, StatusFollowUp.ENVIADO, None)
        eventos = [
            _evento(
                lead.id,
                TipoEvento.FOLLOWUP_ENVIADO,
                agora,
                tipo=followup.tipo.value,
                etapa=followup.etapa,
                requer_template=template,
                itens=[i.id for i in itens],
            )
        ]
        if etapa.encerramento:
            eventos.append(
                _evento(lead.id, TipoEvento.LEAD_ENCERRADO_INATIVIDADE, agora, etapa=followup.etapa)
            )
        await self._eventos.registrar(eventos)
        if not etapa.encerramento:
            await self._programar.agendar_etapa(
                lead, situacao, followup.etapa + 1, ignorar=followup.ignorar_horario
            )

    async def _item_novo(self, lead: Lead, historico: Sequence[Mensagem]) -> list[ItemCatalogo]:
        """Algo de valor: um item compatível com a ficha que ainda não foi apresentado."""
        q = lead.qualificacao
        if self._consulta is None or q.intencao_atual is None:
            return []
        consulta = self._consulta(q.intencao_atual, q.ficha)
        if consulta is None:
            return []
        ja_vistos = set(itens_citados(historico))
        try:
            resultados = await self._catalogo.buscar(consulta)
        except Exception:
            logger.exception("Busca de item novo para o follow-up do lead %s falhou", lead.id)
            return []
        novos = [r.item for r in resultados if r.item.id not in ja_vistos]
        return novos[:1]

    async def _redigir_sem_inventar(
        self,
        pedido: PedidoMensagemAtiva,
        itens: Sequence[ItemCatalogo],
        historico: Sequence[Mensagem],
    ) -> tuple[str, MensagemAtiva]:
        """Nada fora da base: códigos citados precisam ser do item novo ou já apresentados."""
        permitidos = {i.id for i in itens} | set(itens_citados(historico))
        resposta = await self._redator.redigir(pedido)
        invalidos = [
            c for c in self._persona.codigos_citados(resposta.texto) if c not in permitidos
        ]
        if invalidos:
            correcao = (
                f"ATENÇÃO: os códigos {', '.join(invalidos)} NÃO existem para este lead. "
                "Reescreva sem citá-los."
            )
            resposta = await self._redator.redigir(replace(pedido, instrucao_adicional=correcao))
            if any(c not in permitidos for c in self._persona.codigos_citados(resposta.texto)):
                resposta = await self._redator.redigir(replace(pedido, itens_novos=()))
        return resposta.texto, resposta

    # ------------------------------------------------------------------ lembrete
    async def _lembrar(
        self,
        followup: FollowUp,
        lead: Lead,
        conversa: Conversa,
        historico: Sequence[Mensagem],
        *,
        agendamento: Agendamento | None,
        ultima_do_lead: datetime | None,
    ) -> None:
        modelo = self._config.lembrete
        if modelo is None or agendamento is None or str(agendamento.id) != followup.referencia:
            motivo = "sem_modelo_de_lembrete" if modelo is None else "agendamento_mudou"
            await self._followups.concluir(followup.id, StatusFollowUp.CANCELADO, motivo)
            return
        resposta = await self._redator.redigir(
            PedidoMensagemAtiva(lead, historico, modelo.objetivo, agendamento=agendamento)
        )
        agora = self._relogio.agora()
        template = requer_template(ultima_do_lead, agora, self._config.janela_conversa)
        await self._enviar(
            lead,
            conversa,
            resposta.texto,
            {
                "followup": {"tipo": followup.tipo.value, "agendamento_id": followup.referencia},
                "requer_template": template,
                "template": modelo.template,
                "modelo": resposta.modelo,
            },
            template=modelo.template if template else None,
        )
        await self._followups.concluir(followup.id, StatusFollowUp.ENVIADO, None)
        await self._eventos.registrar(
            [
                _evento(
                    lead.id,
                    TipoEvento.FOLLOWUP_ENVIADO,
                    agora,
                    tipo=followup.tipo.value,
                    agendamento_id=followup.referencia,
                    requer_template=template,
                )
            ]
        )

    # ------------------------------------------------------------------ SLA
    async def _checar_sla(self, followup: FollowUp, lead: Lead) -> None:
        atendimento = lead.atendimento_atual
        desde = atendimento.na_fila_desde
        horario = self._config.horario
        ainda_na_fila = (
            atendimento.estado is EstadoAtendimento.AGUARDANDO_HUMANO
            and desde is not None
            and desde.isoformat() == followup.referencia
        )
        if not ainda_na_fila or horario is None or desde is None:
            await self._followups.concluir(followup.id, StatusFollowUp.CANCELADO, "saiu_da_fila")
            return
        agora = self._relogio.agora()
        proxima = proxima_checagem_sla(desde, self._config.sla_handoff, agora, horario)
        if proxima is not None:
            await self._followups.reagendar(followup.adiar(proxima, "dentro_do_sla"))
            return
        espera = tempo_util(desde, agora, horario)
        await self._followups.concluir(followup.id, StatusFollowUp.ENVIADO, None)
        await self._eventos.registrar(
            [
                _evento(
                    lead.id,
                    TipoEvento.HANDOFF_SLA_EXCEDIDO,
                    agora,
                    na_fila_desde=desde.isoformat(),
                    espera_util_minutos=int(espera.total_seconds() // 60),
                    sla_minutos=int(self._config.sla_handoff.total_seconds() // 60),
                )
            ]
        )

    # ------------------------------------------------------------------ envio
    async def _enviar(
        self,
        lead: Lead,
        conversa: Conversa,
        texto: str,
        metadados: dict[str, object],
        *,
        template: str | None,
    ) -> None:
        """`template`: fora da janela de conversa, envia pelo template da vertical."""
        momento = self._relogio.agora()
        mensagem = Mensagem.nova(
            conversa.id, Papel.ASSISTENTE, texto, metadados=metadados, criada_em=momento
        )
        await self._conversas.adicionar_mensagem(mensagem)
        await self._conversas.salvar(conversa.tocar(momento))
        canal = self._canais.get(lead.canal)
        if canal is None:
            logger.error("Sem CanalMensagemPort para o canal %s", lead.canal)
        elif template:
            await canal.enviar_template(lead, template, {"texto": texto})
        else:
            await canal.enviar(lead, mensagem)
