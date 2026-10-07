"""Porta de entrada única de mensagens de leads — web hoje, WhatsApp depois."""

import logging
from dataclasses import dataclass, replace

from sdr.core.application.dto.mensagem_recebida import MensagemRecebida
from sdr.core.application.ports.agente import AgenteConversacionalPort, EntradaAgente
from sdr.core.application.ports.catalogo import CatalogoPort
from sdr.core.application.ports.repositorios import (
    ConversaRepository,
    LeadEventoRepository,
    LeadRepository,
)
from sdr.core.domain.agente import Persona, RespostaAgente
from sdr.core.domain.catalogo import ItemCatalogo
from sdr.core.domain.conversa import Conversa, Lead, Mensagem, Papel
from sdr.core.domain.eventos import EventoLead, TipoEvento

logger = logging.getLogger(__name__)

JANELA_HISTORICO_PADRAO = 30
LIMITE_TEXTO = 4000


class MensagemInvalidaError(ValueError):
    pass


@dataclass(frozen=True)
class ResultadoProcessamento:
    lead: Lead
    conversa: Conversa
    resposta: Mensagem
    itens_sugeridos: tuple[ItemCatalogo, ...]
    campos_faltantes: tuple[str, ...] = ()


class ProcessarMensagemRecebida:
    def __init__(
        self,
        leads: LeadRepository,
        conversas: ConversaRepository,
        agente: AgenteConversacionalPort,
        catalogo: CatalogoPort,
        *,
        eventos: LeadEventoRepository,
        persona: Persona,
        janela_historico: int = JANELA_HISTORICO_PADRAO,
    ) -> None:
        self._leads = leads
        self._eventos = eventos
        self._conversas = conversas
        self._agente = agente
        self._catalogo = catalogo
        self._persona = persona
        self._janela_historico = janela_historico

    async def executar(self, mensagem: MensagemRecebida) -> ResultadoProcessamento:
        texto = mensagem.texto.strip()
        if not texto:
            raise MensagemInvalidaError("mensagem vazia")
        if len(texto) > LIMITE_TEXTO:
            raise MensagemInvalidaError(f"mensagem acima de {LIMITE_TEXTO} caracteres")

        lead = await self._obter_ou_criar_lead(mensagem)
        conversa = await self._obter_ou_abrir_conversa(lead)
        historico = await self._conversas.ultimas_mensagens(conversa.id, self._janela_historico)

        # A mensagem do lead é gravada antes de chamar o LLM: nunca se perde o que o lead disse.
        recebida = Mensagem.nova(
            conversa.id,
            Papel.LEAD,
            texto,
            metadados={"canal": mensagem.canal.value, **dict(mensagem.metadados)},
            criada_em=mensagem.timestamp,
        )
        await self._conversas.adicionar_mensagem(recebida)

        entrada = EntradaAgente(lead=lead, historico=historico, texto=texto)
        resposta, itens, invalidos, fallback = await self._responder_sem_inventar(entrada)

        enviada = Mensagem.nova(
            conversa.id,
            Papel.AGENTE,
            resposta.texto,
            self._metadados(resposta, itens, invalidos, fallback),
        )
        await self._conversas.adicionar_mensagem(enviada)
        conversa = conversa.tocar(enviada.criada_em)
        await self._conversas.salvar(conversa)

        if resposta.qualificacao is not None and resposta.qualificacao != lead.qualificacao:
            lead = replace(lead, qualificacao=resposta.qualificacao)
            await self._leads.salvar(lead)
        await self._eventos.registrar(resposta.eventos)

        return ResultadoProcessamento(lead, conversa, enviada, itens, resposta.campos_faltantes)

    async def _obter_ou_criar_lead(self, mensagem: MensagemRecebida) -> Lead:
        lead = await self._leads.obter_por_remetente(mensagem.canal, mensagem.remetente_id)
        if lead is None:
            lead = Lead.novo(mensagem.canal, mensagem.remetente_id)
            await self._leads.salvar(lead)
            await self._eventos.registrar(
                [
                    EventoLead(
                        lead.id,
                        TipoEvento.LEAD_CRIADO,
                        lead.criado_em,
                        {"canal": lead.canal.value, "remetente_id": lead.remetente_id},
                    )
                ]
            )
        return lead

    async def _obter_ou_abrir_conversa(self, lead: Lead) -> Conversa:
        conversa = await self._conversas.obter_aberta(lead.id)
        if conversa is None:
            conversa = Conversa.nova(lead)
            await self._conversas.salvar(conversa)
        return conversa

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
