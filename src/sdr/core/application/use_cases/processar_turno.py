"""Processa o TURNO de um lead: agrega as mensagens pendentes e responde ao conjunto.

1. Trava o lead (nunca dois turnos do mesmo lead em paralelo); se não conseguir, reagenda.
2. Agrega as mensagens PENDENTES em ordem e executa o agente UMA vez.
3. Se chegaram mensagens novas enquanto o agente pensava (resposta ainda não enviada),
   descarta a resposta e reprocessa com tudo.
4. Persiste a resposta, marca o lote como PROCESSADO, grava qualificação, negociação de
   agenda e eventos, e entrega pelo CanalMensagemPort do canal do lead.
"""

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from uuid import UUID

from sdr.core.application.ports.agente import AgenteConversacionalPort, EntradaAgente
from sdr.core.application.ports.canal import CanalMensagemPort
from sdr.core.application.ports.catalogo import CatalogoPort
from sdr.core.application.ports.repositorios import (
    ConversaRepository,
    LeadEventoRepository,
    LeadRepository,
)
from sdr.core.application.ports.turnos import AgendadorTurnoPort, TravaTurnoPort
from sdr.core.domain.agente import Persona, RespostaAgente
from sdr.core.domain.catalogo import ItemCatalogo
from sdr.core.domain.conversa import Canal, Conversa, Lead, Mensagem, Papel, StatusMensagem
from sdr.core.domain.conversa import agora as agora_utc

logger = logging.getLogger(__name__)

JANELA_HISTORICO_PADRAO = 30
MAX_REPROCESSAMENTOS = 3


@dataclass(frozen=True)
class ResultadoTurno:
    lead: Lead
    conversa: Conversa
    lote: tuple[Mensagem, ...]
    resposta: Mensagem
    itens_sugeridos: tuple[ItemCatalogo, ...]
    campos_faltantes: tuple[str, ...] = ()
    reprocessamentos: int = 0


class ProcessarTurno:
    def __init__(
        self,
        leads: LeadRepository,
        conversas: ConversaRepository,
        agente: AgenteConversacionalPort,
        catalogo: CatalogoPort,
        *,
        eventos: LeadEventoRepository,
        persona: Persona,
        trava: TravaTurnoPort,
        agendador: AgendadorTurnoPort,
        canais: Mapping[Canal, CanalMensagemPort],
        janela_historico: int = JANELA_HISTORICO_PADRAO,
        max_reprocessamentos: int = MAX_REPROCESSAMENTOS,
    ) -> None:
        self._leads = leads
        self._conversas = conversas
        self._agente = agente
        self._catalogo = catalogo
        self._eventos = eventos
        self._persona = persona
        self._trava = trava
        self._agendador = agendador
        self._canais = canais
        self._janela_historico = janela_historico
        self._max_reprocessamentos = max_reprocessamentos

    async def executar(self, lead_id: UUID) -> ResultadoTurno | None:
        async with self._trava.travar(lead_id) as obtida:
            if not obtida:
                logger.info("Turno do lead %s já em andamento; reagendando", lead_id)
                self._agendador.agendar(lead_id)
                return None
            return await self._processar(lead_id)

    async def _processar(self, lead_id: UUID) -> ResultadoTurno | None:
        lead = await self._leads.obter(lead_id)
        conversa = await self._conversas.obter_aberta(lead_id) if lead else None
        if lead is None or conversa is None:
            return None

        for tentativa in range(self._max_reprocessamentos + 1):
            lote = await self._conversas.pendentes(conversa.id)
            if not lote:
                return None
            ids_lote = {m.id for m in lote}
            historico = await self._conversas.ultimas_mensagens(conversa.id, self._janela_historico)
            entrada = EntradaAgente(
                lead=lead, historico=historico, texto="\n".join(m.texto for m in lote)
            )
            try:
                resposta, itens, invalidos, fallback = await self._responder_sem_inventar(entrada)
            except Exception:
                logger.exception("Falha no turno do lead %s; lote marcado como FALHA", lead_id)
                await self._conversas.marcar_status([m.id for m in lote], StatusMensagem.FALHA)
                return None

            novas = [
                m for m in await self._conversas.pendentes(conversa.id) if m.id not in ids_lote
            ]
            if novas and tentativa < self._max_reprocessamentos:
                logger.info(
                    "Lead %s mandou %d mensagem(ns) durante o turno; reprocessando",
                    lead_id,
                    len(novas),
                )
                continue
            return await self._concluir(
                lead,
                conversa,
                lote,
                resposta,
                itens=itens,
                invalidos=invalidos,
                fallback=fallback,
                reprocessamentos=tentativa,
            )
        return None  # inalcançável: a última tentativa sempre conclui

    async def _concluir(
        self,
        lead: Lead,
        conversa: Conversa,
        lote: Sequence[Mensagem],
        resposta: RespostaAgente,
        *,
        itens: tuple[ItemCatalogo, ...],
        invalidos: list[str],
        fallback: bool,
        reprocessamentos: int,
    ) -> ResultadoTurno:
        metadados = self._metadados(resposta, itens, invalidos, fallback)
        metadados["responde_a"] = [str(m.id) for m in lote]
        enviada = Mensagem.nova(
            conversa.id, Papel.AGENTE, resposta.texto, metadados=metadados, criada_em=agora_utc()
        )
        await self._conversas.adicionar_mensagem(enviada)
        await self._conversas.marcar_status([m.id for m in lote], StatusMensagem.PROCESSADA)
        conversa = conversa.tocar(enviada.criada_em)
        await self._conversas.salvar(conversa)

        atualizado = lead
        if resposta.qualificacao is not None:
            atualizado = replace(atualizado, qualificacao=resposta.qualificacao)
        if resposta.agenda is not None:
            atualizado = replace(atualizado, agenda=resposta.agenda)
        if atualizado != lead:
            lead = atualizado
            await self._leads.salvar(lead)
        await self._eventos.registrar(resposta.eventos)

        canal = self._canais.get(lead.canal)
        if canal is None:
            logger.error("Sem CanalMensagemPort para o canal %s", lead.canal)
        else:
            await canal.enviar(lead, enviada)

        return ResultadoTurno(
            lead,
            conversa,
            tuple(lote),
            enviada,
            itens,
            resposta.campos_faltantes,
            reprocessamentos,
        )

    # ------------------------------------------------------------------ nada fora da base
    async def _responder_sem_inventar(
        self, entrada: EntradaAgente
    ) -> tuple[RespostaAgente, tuple[ItemCatalogo, ...], list[str], bool]:
        """Garante que todo código de item citado existe na base.

        Se o agente citar algo inexistente, pede UMA correção; persistindo, responde com
        a mensagem de fallback da persona (melhor não sugerir nada do que inventar).
        """
        resposta = await self._agente.responder(entrada)
        itens, invalidos = await self._conferir_citacoes(resposta)
        if not invalidos:
            return resposta, itens, [], False

        logger.warning("Agente citou códigos inexistentes: %s — pedindo correção", invalidos)
        correcao = (
            f"ATENÇÃO: os códigos {', '.join(invalidos)} NÃO existem na base. Reescreva a "
            "resposta citando somente itens devolvidos pela ferramenta de busca."
        )
        resposta = await self._agente.responder(replace(entrada, instrucao_adicional=correcao))
        itens, invalidos = await self._conferir_citacoes(resposta)
        if not invalidos:
            return resposta, itens, [], False

        logger.error("Agente insistiu em códigos inexistentes: %s — usando fallback", invalidos)
        return replace(resposta, texto=self._persona.mensagem_fallback), (), invalidos, True

    async def _conferir_citacoes(
        self, resposta: RespostaAgente
    ) -> tuple[tuple[ItemCatalogo, ...], list[str]]:
        """Retorna (itens citados que existem, na ordem citada; códigos que não existem).

        Sem padrão de código na persona, considera sugeridos todos os itens consultados.
        """
        conhecidos = {i.id: i for i in resposta.itens_consultados}
        if not self._persona.padrao_codigo_item:
            return tuple(conhecidos.values()), []

        citados = self._persona.codigos_citados(resposta.texto)
        a_confirmar = [c for c in citados if c not in conhecidos]  # ex.: citado em turno anterior
        if a_confirmar:
            conhecidos.update({i.id: i for i in await self._catalogo.obter(a_confirmar)})
        invalidos = [c for c in citados if c not in conhecidos]
        return tuple(conhecidos[c] for c in citados if c in conhecidos), invalidos

    def _metadados(
        self,
        resposta: RespostaAgente,
        itens: tuple[ItemCatalogo, ...],
        invalidos: list[str],
        fallback: bool,
    ) -> dict[str, object]:
        metadados: dict[str, object] = {
            "prompt_versao": self._persona.versao_prompt,
            "modelo": resposta.modelo,
            "tokens": {"entrada": resposta.tokens_entrada, "saida": resposta.tokens_saida},
            "itens_citados": [{"id": i.id, "titulo": i.titulo, "resumo": i.resumo} for i in itens],
            "ferramentas": [
                {"nome": c.nome, "argumentos": c.argumentos, "erro": c.erro}
                for c in resposta.chamadas
            ],
        }
        q = resposta.qualificacao
        if q is not None:
            metadados["qualificacao"] = {
                "intencao": q.intencao_atual,
                "campos_faltantes": list(resposta.campos_faltantes),
                "score": q.score.pontos if q.score else None,
                "proxima_acao": q.proxima_acao,
            }
        if resposta.metadados:
            metadados["agente"] = resposta.metadados
        if invalidos:
            metadados["codigos_invalidos"] = invalidos
        if fallback:
            metadados["fallback"] = True
        return metadados
