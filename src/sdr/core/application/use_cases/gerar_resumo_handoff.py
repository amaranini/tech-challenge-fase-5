"""Gera o resumo de handoff do lead para o responsável e o registra no CRM.

Roda FORA do turno da conversa: é assinante do `PublicadorEventosPort`, que entrega os
eventos depois que a resposta do assistente já foi enviada.

- Gatilhos iniciais (ex.: LeadQualificado, AgendamentoCriado; HandoffConfirmado na
  Etapa C) geram a primeira versão.
- Gatilhos de atualização (ficha corrigida, agendamento remarcado...) só regeneram se já
  existe resumo.
- Nova versão só se os fatos mudaram (impressão digital): o mesmo estado não vira v2.
- O LLM redige; a ancoragem do domínio remove o que não tem lastro; um verificador
  independente julga cada frase dos textos livres e o domínio poda as sem sustentação
  (texto livre escapava da ancoragem mecânica — visto com LLM real); o CRM recebe a versão.
"""

import logging
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from uuid import UUID, uuid4

from sdr.core.application.ports.agenda import AgendaPort
from sdr.core.application.ports.crm import CRMPort
from sdr.core.application.ports.relogio import RelogioPort
from sdr.core.application.ports.repositorios import (
    ConversaRepository,
    LeadEventoRepository,
    LeadRepository,
)
from sdr.core.application.ports.resumo import RedatorResumoPort, ResumoRepository
from sdr.core.domain.agenda import Agendamento
from sdr.core.domain.conversa import Lead
from sdr.core.domain.eventos import EventoLead, TipoEvento
from sdr.core.domain.qualificacao import IntencaoVertical, campos_faltantes
from sdr.core.domain.resumo import (
    NAO_INFORMADO,
    FatosResumo,
    Resumo,
    TemplateResumo,
    TipoSecao,
    ancorar,
    itens_citados,
    podar_textos,
)

logger = logging.getLogger(__name__)

GATILHOS_INICIAIS = frozenset({TipoEvento.LEAD_QUALIFICADO, TipoEvento.AGENDAMENTO_CRIADO})
GATILHOS_ATUALIZACAO = frozenset(
    {
        TipoEvento.INTENCAO_ALTERADA,
        TipoEvento.CAMPO_PREENCHIDO,
        TipoEvento.CAMPO_CORRIGIDO,
        TipoEvento.CAMPO_REMOVIDO,
        TipoEvento.AGENDAMENTO_REMARCADO,
        TipoEvento.AGENDAMENTO_CANCELADO,
    }
)
LIMITE_MENSAGENS = 200


@dataclass(frozen=True)
class ResultadoResumo:
    resumo: Resumo
    nova_versao: bool


class GerarResumoHandoff:
    def __init__(
        self,
        leads: LeadRepository,
        conversas: ConversaRepository,
        eventos: LeadEventoRepository,
        *,
        resumos: ResumoRepository,
        redator: RedatorResumoPort,
        crm: CRMPort,
        agenda: AgendaPort,
        relogio: RelogioPort,
        template: TemplateResumo,
        intencoes: Sequence[IntencaoVertical],
        gatilhos_iniciais: Collection[TipoEvento] = GATILHOS_INICIAIS,
        gatilhos_atualizacao: Collection[TipoEvento] = GATILHOS_ATUALIZACAO,
    ) -> None:
        self._leads = leads
        self._conversas = conversas
        self._eventos = eventos
        self._resumos = resumos
        self._redator = redator
        self._crm = crm
        self._agenda = agenda
        self._relogio = relogio
        self._template = template
        self._intencoes: Mapping[str, IntencaoVertical] = {i.nome: i for i in intencoes}
        self._iniciais = frozenset(gatilhos_iniciais)
        self._atualizacao = frozenset(gatilhos_atualizacao)

    async def ao_publicar(self, eventos: Sequence[EventoLead]) -> None:
        """Assinante do PublicadorEventosPort: um resumo por lead, pelo gatilho mais forte."""
        por_lead: dict[UUID, list[EventoLead]] = {}
        for evento in eventos:
            por_lead.setdefault(evento.lead_id, []).append(evento)
        for lead_id, do_lead in por_lead.items():
            iniciais = [e for e in do_lead if e.tipo in self._iniciais]
            atualizacoes = [e for e in do_lead if e.tipo in self._atualizacao]
            if iniciais:
                await self.executar(lead_id, iniciais[-1].tipo.value)
            elif atualizacoes and await self._resumos.ultimo(lead_id) is not None:
                await self.executar(lead_id, atualizacoes[-1].tipo.value)

    async def executar(self, lead_id: UUID, gatilho: str) -> ResultadoResumo | None:
        lead = await self._leads.obter(lead_id)
        if lead is None:
            return None
        fatos = await self._fatos(lead)
        anterior = await self._resumos.ultimo(lead_id)
        impressao = fatos.impressao_digital()
        if anterior is not None and anterior.impressao == impressao:
            logger.info("Resumo do lead %s já reflete os fatos (v%d)", lead_id, anterior.versao)
            return ResultadoResumo(anterior, nova_versao=False)

        rascunho = await self._redator.redigir(fatos, self._template)
        secoes, descartados = ancorar(rascunho.secoes, self._template, fatos)
        textos = {
            s.chave: str(s.conteudo)
            for s in secoes
            if s.tipo is TipoSecao.TEXTO and s.conteudo != NAO_INFORMADO
        }
        if textos:
            checagem = await self._redator.checar(fatos, textos)
            secoes, podados = podar_textos(secoes, checagem, fatos)
            descartados = (*descartados, *podados)
        if descartados:
            logger.warning("Resumo do lead %s: ancoragem descartou %s", lead_id, descartados)
        resumo = Resumo(
            id=uuid4(),
            lead_id=lead_id,
            versao=(anterior.versao + 1) if anterior else 1,
            gerado_em=self._relogio.agora(),
            gatilho=gatilho,
            template_versao=self._template.versao,
            titulo=self._template.titulo,
            secoes=secoes,
            impressao=impressao,
            descartados=descartados,
            modelo=rascunho.modelo,
            tokens_entrada=rascunho.tokens_entrada,
            tokens_saida=rascunho.tokens_saida,
        )
        await self._resumos.salvar(resumo)
        registro = await self._crm.registrar(lead, resumo, fatos.agendamento)
        await self._eventos.registrar(
            [
                EventoLead(
                    lead_id,
                    TipoEvento.RESUMO_GERADO,
                    resumo.gerado_em,
                    {
                        "versao": resumo.versao,
                        "gatilho": gatilho,
                        "crm_id": registro.crm_id,
                        "descartados": len(descartados),
                    },
                )
            ]
        )
        return ResultadoResumo(resumo, nova_versao=True)

    async def _fatos(self, lead: Lead) -> FatosResumo:
        conversa = await self._conversas.obter_aberta(lead.id)
        mensagens = (
            await self._conversas.ultimas_mensagens(conversa.id, LIMITE_MENSAGENS)
            if conversa
            else []
        )
        q = lead.qualificacao
        intencao = self._intencoes.get(q.intencao_atual or "")
        faltantes = campos_faltantes(q.ficha, intencao.prioridade_campos) if intencao else []
        return FatosResumo(
            lead_id=lead.id,
            intencao=q.intencao_atual,
            ficha=dict(q.ficha),
            campos_faltantes=tuple(faltantes),
            score=q.score,
            proxima_acao=q.proxima_acao,
            agendamento=await self._agendamento(lead.id),
            mensagens=tuple(mensagens),
            itens=itens_citados(mensagens),
        )

    async def _agendamento(self, lead_id: UUID) -> Agendamento | None:
        """O agendamento mais recente do lead (ativo ou cancelado: ambos informam)."""
        todos = await self._agenda.listar_agendamentos(lead_id=lead_id)
        return max(todos, key=lambda a: a.criado_em, default=None)
