"""Processa o TURNO de um lead: agrega as mensagens pendentes e responde ao conjunto.

1. Trava o lead (nunca dois turnos do mesmo lead em paralelo); se não conseguir, reagenda.
2. Agrega as mensagens PENDENTES em ordem e executa o agente UMA vez.
3. Se chegaram mensagens novas enquanto o agente pensava (resposta ainda não enviada),
   descarta a resposta e reprocessa com tudo.
4. Atendimento humano em curso: a IA fica em silêncio (o lote só é registrado).
5. Antes de enviar, RECHECA o estado de atendimento: se mudou enquanto o grafo pensava
   (ex.: um humano assumiu), descarta a resposta. A ação de atendimento do turno (pedir
   handoff, confirmar, voltar para a IA...) é aplicada com compare-and-set.
6. Persiste a resposta, marca o lote como PROCESSADO, grava qualificação, negociação de
   agenda e eventos, e entrega pelo canal do lead (EntregarMensagem: janela/template).
   Lote só com anexos (áudio, imagem...): responde o aviso da persona, sem LLM.
7. Reprograma o follow-up (a cadência conta desta resposta) ou o encerra no opt-out.
8. Publica os eventos do turno para as reações fora da conversa (PublicadorEventosPort).
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass, replace
from uuid import UUID

from sdr.core.application.ports.agente import AgenteConversacionalPort, EntradaAgente
from sdr.core.application.ports.catalogo import CatalogoPort
from sdr.core.application.ports.eventos import PublicadorEventosPort
from sdr.core.application.ports.observabilidade import (
    ContextoRastro,
    RastreioNulo,
    RastreioPort,
    Rastro,
)
from sdr.core.application.ports.repositorios import (
    ConversaRepository,
    LeadEventoRepository,
    LeadRepository,
)
from sdr.core.application.ports.turnos import AgendadorTurnoPort, TravaTurnoPort
from sdr.core.application.use_cases.atendimento import (
    AplicarAcaoAtendimento,
    AtendimentoAlteradoError,
)
from sdr.core.application.use_cases.entregar_mensagem import EntregarMensagem
from sdr.core.application.use_cases.followup import ProgramarFollowUps
from sdr.core.domain.agente import Persona, RespostaAgente
from sdr.core.domain.atendimento import EstadoAtendimento
from sdr.core.domain.catalogo import ItemCatalogo
from sdr.core.domain.conversa import Conversa, Lead, Mensagem, Papel, StatusMensagem, agora
from sdr.core.domain.eventos import EventoLead, TipoEvento
from sdr.core.domain.pii import mascarar_cpf
from sdr.core.domain.seguranca import promessas_indevidas

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
        entrega: EntregarMensagem,
        janela_historico: int = JANELA_HISTORICO_PADRAO,
        max_reprocessamentos: int = MAX_REPROCESSAMENTOS,
        publicador: PublicadorEventosPort | None = None,
        atendimento: AplicarAcaoAtendimento | None = None,
        followups: ProgramarFollowUps | None = None,
        rastreio: RastreioPort | None = None,
    ) -> None:
        self._rastreio: RastreioPort = rastreio or RastreioNulo()
        self._followups = followups
        self._publicador = publicador
        self._atendimento = atendimento
        self._leads = leads
        self._conversas = conversas
        self._agente = agente
        self._catalogo = catalogo
        self._eventos = eventos
        self._persona = persona
        self._trava = trava
        self._agendador = agendador
        self._entrega = entrega
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
        contexto = ContextoRastro(
            "turno",
            lead.id,
            lead.canal.value,
            lead.atendimento_atual.estado.value,
            lead.qualificacao.intencao_atual,
            metadados={"prompt_persona": self._persona.versao_prompt},
        )
        with self._rastreio.rastrear(contexto) as rastro:
            resultado = await self._turno(lead, conversa, rastro)
            if resultado is not None:
                rastro.saida = resultado.resposta.texto
                rastro.metadados_finais = {
                    "intencao_final": resultado.lead.qualificacao.intencao_atual or "indefinida",
                    "reprocessamentos": str(resultado.reprocessamentos),
                }
            return resultado

    async def _turno(self, lead: Lead, conversa: Conversa, rastro: Rastro) -> ResultadoTurno | None:
        lead_id = lead.id
        for tentativa in range(self._max_reprocessamentos + 1):
            lote = await self._conversas.pendentes(conversa.id)
            if not lote:
                return None
            ids_lote = {m.id for m in lote}
            historico = await self._conversas.ultimas_mensagens(conversa.id, self._janela_historico)
            entrada = EntradaAgente(
                lead=lead,
                # Minimização: o CPF não serve para a conversa e não vai para o LLM.
                historico=[replace(m, texto=mascarar_cpf(m.texto)) for m in historico],
                texto=mascarar_cpf(_texto_do_lote(lote)),
            )
            rastro.entrada = entrada.texto  # as mensagens do lote, agregadas
            if lead.atendimento_atual.estado is not EstadoAtendimento.ATENDIMENTO_HUMANO:
                # O lead parou de digitar: a Lia "começa a digitar" (WhatsApp) enquanto pensa.
                await self._entrega.indicar_digitando(lead, lote)
            itens: tuple[ItemCatalogo, ...] = ()
            invalidos: list[str] = []
            fallback = False
            try:
                if all(m.metadados.get("somente_midia") for m in lote):
                    resposta = self._aviso_midia(lead)
                else:
                    resposta, itens, invalidos, fallback = await self._responder_sem_inventar(
                        entrada
                    )
            except Exception:
                logger.exception("Falha no turno do lead %s; lote marcado como FALHA", lead_id)
                await self._conversas.marcar_status([m.id for m in lote], StatusMensagem.FALHA)
                return None

            if resposta.silenciar:
                logger.info("Lead %s em atendimento humano: IA em silêncio", lead_id)
                await self._conversas.marcar_status([m.id for m in lote], StatusMensagem.PROCESSADA)
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
                rastro=rastro,
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
        rastro: Rastro | None = None,
    ) -> ResultadoTurno | None:
        if not await self._estado_ainda_vale(lead, resposta):
            await self._conversas.marcar_status([m.id for m in lote], StatusMensagem.PROCESSADA)
            return None
        metadados = self._metadados(resposta, itens, invalidos, fallback)
        metadados["responde_a"] = [str(m.id) for m in lote]
        if rastro is not None and rastro.trace_id:
            metadados["trace"] = {"id": rastro.trace_id, "url": rastro.url}
        # O lead acabou de falar: sempre dentro da janela (texto livre); a regra é do core.
        enviada = await self._entrega.preparar(
            lead, conversa, Papel.ASSISTENTE, resposta.texto, metadados
        )
        await self._conversas.adicionar_mensagem(enviada)
        await self._conversas.marcar_status([m.id for m in lote], StatusMensagem.PROCESSADA)
        conversa = conversa.tocar(enviada.criada_em)
        await self._conversas.salvar(conversa)

        atualizado = await self._leads.obter(lead.id) or lead  # atendimento recém-gravado
        lead = replace(lead, atendimento=atualizado.atendimento, opt_out_em=atualizado.opt_out_em)
        atualizado = lead
        if resposta.qualificacao is not None:
            atualizado = replace(atualizado, qualificacao=resposta.qualificacao)
        if resposta.agenda is not None:
            atualizado = replace(atualizado, agenda=resposta.agenda)
        if atualizado != lead:
            lead = atualizado
            await self._leads.salvar(lead)
        await self._eventos.registrar(resposta.eventos)

        await self._entrega.transmitir(lead, enviada)
        # Follow-up: a cadência recomeça desta resposta (ou acaba, se o lead pediu para parar).
        if self._followups is not None:
            if resposta.opt_out:
                await self._followups.registrar_opt_out(lead, "\n".join(m.texto for m in lote))
            else:
                await self._followups.apos_resposta(lead)
        # Reações aos eventos (ex.: resumo para o responsável) rodam fora do turno.
        if self._publicador is not None and resposta.eventos:
            self._publicador.publicar(resposta.eventos)

        return ResultadoTurno(
            lead,
            conversa,
            tuple(lote),
            enviada,
            itens,
            resposta.campos_faltantes,
            reprocessamentos,
        )

    def _aviso_midia(self, lead: Lead) -> RespostaAgente:
        """Só anexos no lote: o assistente ainda não entende áudio/imagem — avisa, sem LLM.
        Com uma pessoa da equipe no atendimento, a IA continua em silêncio."""
        if lead.atendimento_atual.estado is EstadoAtendimento.ATENDIMENTO_HUMANO:
            return RespostaAgente(texto="", silenciar=True)
        return RespostaAgente(texto=self._persona.aviso_midia, metadados={"aviso_midia": True})

    async def _estado_ainda_vale(self, lead: Lead, resposta: RespostaAgente) -> bool:
        """Recheca o atendimento antes de enviar e aplica a ação do turno (compare-and-set).
        False = algo mudou no meio (ex.: humano assumiu): a resposta é descartada."""
        esperado = lead.atendimento_atual.estado
        atual = await self._leads.obter(lead.id)
        if atual is None or atual.atendimento_atual.estado is not esperado:
            logger.warning(
                "Atendimento do lead %s mudou durante o turno (%s → %s): resposta descartada",
                lead.id,
                esperado.value,
                atual.atendimento_atual.estado.value if atual else "?",
            )
            return False
        if resposta.acao_atendimento is None or self._atendimento is None:
            return True
        try:
            await self._atendimento.executar(lead.id, resposta.acao_atendimento, esperado)
        except AtendimentoAlteradoError:
            logger.warning("Atendimento do lead %s mudou ao aplicar a ação do turno", lead.id)
            return False
        return True

    # ------------------------------------------------------------------ nada fora da base
    async def _responder_sem_inventar(
        self, entrada: EntradaAgente
    ) -> tuple[RespostaAgente, tuple[ItemCatalogo, ...], list[str], bool]:
        """Validação de saída (ADR 015): todo código de item citado existe na base e não há
        promessa comercial fora dela (desconto, garantia...).

        Reprovada, pede UMA correção; persistindo, responde com a mensagem segura da persona
        (melhor não sugerir nada do que inventar). Reprovação vira evento.
        """
        resposta = await self._agente.responder(entrada)
        itens, invalidos, promessas = await self._validar_saida(resposta)
        if not invalidos and not promessas:
            return resposta, itens, [], False

        logger.warning(
            "Resposta reprovada (códigos inexistentes: %s; promessas: %s) — pedindo correção",
            invalidos,
            list(promessas),
        )
        problemas: list[str] = []
        if invalidos:
            problemas.append(
                f"os códigos {', '.join(invalidos)} NÃO existem na base: cite somente itens "
                "devolvidos pela ferramenta de busca"
            )
        if promessas:
            problemas.append(
                "a resposta prometeu desconto, garantia ou condição que não está na base: não "
                "prometa nada disso (negociação é com a equipe)"
            )
        correcao = f"ATENÇÃO — reescreva a resposta: {'; '.join(problemas)}."
        primeira = (invalidos, promessas)
        resposta = await self._agente.responder(replace(entrada, instrucao_adicional=correcao))
        itens, invalidos, promessas = await self._validar_saida(resposta)
        if not invalidos and not promessas:
            return self._com_reprovacao(entrada, resposta, *primeira, False), itens, [], False

        logger.error("Resposta reprovada de novo — usando a mensagem segura da persona")
        segura = replace(resposta, texto=self._persona.mensagem_fallback)
        return (
            self._com_reprovacao(entrada, segura, invalidos, promessas, True),
            (),
            invalidos,
            True,
        )

    async def _validar_saida(
        self, resposta: RespostaAgente
    ) -> tuple[tuple[ItemCatalogo, ...], list[str], tuple[str, ...]]:
        itens, invalidos = await self._conferir_citacoes(resposta)
        promessas = promessas_indevidas(resposta.texto, self._persona.padroes_promessa)
        return itens, invalidos, promessas

    @staticmethod
    def _com_reprovacao(
        entrada: EntradaAgente,
        resposta: RespostaAgente,
        invalidos: list[str],
        promessas: tuple[str, ...],
        fallback: bool,
    ) -> RespostaAgente:
        evento = EventoLead(
            entrada.lead.id,
            TipoEvento.RESPOSTA_REPROVADA,
            agora(),
            {"codigos_invalidos": invalidos, "promessas": list(promessas), "fallback": fallback},
        )
        return replace(resposta, eventos=(*resposta.eventos, evento))

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


def _texto_do_lote(lote: Sequence[Mensagem]) -> str:
    """Falas do lote para o agente. Anexos viram uma nota (o agente não os abre)."""
    textos: list[str] = []
    anexos: list[str] = []
    for m in lote:
        midias = m.metadados.get("midias")
        if isinstance(midias, list) and midias:
            if texto := str(m.metadados.get("texto_lead") or "").strip():
                textos.append(texto)
            anexos.extend(str(midia.get("tipo", "anexo")) for midia in midias)
        else:
            textos.append(m.texto)
    if anexos:
        textos.append(
            f"(o lead também enviou anexo: {', '.join(anexos)} — você não consegue abrir "
            "anexos; se fizer diferença, diga em poucas palavras que por enquanto só lê texto)"
        )
    return "\n".join(textos)
