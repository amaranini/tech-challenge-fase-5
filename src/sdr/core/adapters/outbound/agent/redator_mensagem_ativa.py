"""RedatorMensagemAtivaPort com LLM: follow-up e lembrete na voz da persona, a partir da
conversa (memória do banco), da ficha e — quando houver — de um item novo do catálogo."""

import json
from pathlib import Path
from zoneinfo import ZoneInfo

from sdr.core.application.ports.followup import (
    GanchosTemplate,
    MensagemAtiva,
    PedidoMensagemAtiva,
)
from sdr.core.application.ports.llm import LLMPort, MensagemLLM, PapelLLM
from sdr.core.domain.agenda import DIAS_SEMANA
from sdr.core.domain.agente import Persona
from sdr.core.domain.conversa import Papel
from sdr.core.domain.template import MAX_CARACTERES_VARIAVEL, TemplateLogico

PROMPT_MENSAGEM_ATIVA = (
    Path(__file__).resolve().parent / "prompts" / "mensagem_ativa_v1.md"
).read_text(encoding="utf-8")


class RedatorMensagemAtivaLLM:
    def __init__(self, llm: LLMPort, persona: Persona, fuso: ZoneInfo) -> None:
        self._llm = llm
        self._persona = persona
        self._fuso = fuso

    def _bloco(self, pedido: PedidoMensagemAtiva) -> str:
        q = pedido.lead.qualificacao
        linhas = [
            "[Mensagem ativa — uso interno, nunca mostre isto ao lead]",
            f"Objetivo desta mensagem: {pedido.objetivo}",
            f"Intenção: {q.intencao_atual or 'ainda não definida'}",
            f"Ficha: {json.dumps(dict(q.ficha), ensure_ascii=False, default=str)}",
        ]
        if pedido.encerramento:
            linhas.append(
                "É a ÚLTIMA mensagem da sequência: despeça-se com educação, sem cobrar "
                "resposta, e deixe a porta aberta (é só chamar quando quiser)."
            )
        for item in pedido.itens_novos:
            linhas.append(
                f"Item novo compatível, ainda não apresentado: {item.id} — {item.titulo}. "
                f"{item.resumo} Ao citá-lo, use o código {item.id} exatamente assim."
            )
        if (ag := pedido.agendamento) is not None:
            local = ag.inicio.astimezone(self._fuso)
            linhas.append(
                f"Agendamento: {ag.tipo} com {ag.responsavel.nome} ({ag.responsavel.titulo}), "
                f"{DIAS_SEMANA[local.weekday()]}, {local:%d/%m} às {local:%H:%M}, "
                f"{ag.modalidade}."
            )
        if pedido.instrucao_adicional:
            linhas.append(pedido.instrucao_adicional)
        return "\n".join(linhas)

    def _conversa(self, pedido: PedidoMensagemAtiva) -> list[MensagemLLM]:
        mensagens = [
            MensagemLLM(
                PapelLLM.SISTEMA, f"{self._persona.prompt_sistema}\n\n{PROMPT_MENSAGEM_ATIVA}"
            )
        ]
        for m in pedido.historico:
            if m.papel is Papel.LEAD:
                mensagens.append(MensagemLLM(PapelLLM.USUARIO, m.texto))
            elif m.papel is Papel.ASSISTENTE:
                mensagens.append(MensagemLLM(PapelLLM.ASSISTENTE, m.texto))
            else:
                quem = m.metadados.get("responsavel") or "pessoa da equipe"
                mensagens.append(MensagemLLM(PapelLLM.SISTEMA, f"[{quem}, da equipe]: {m.texto}"))
        mensagens.append(MensagemLLM(PapelLLM.SISTEMA, self._bloco(pedido)))
        return mensagens

    async def redigir(self, pedido: PedidoMensagemAtiva) -> MensagemAtiva:
        resposta = await self._llm.gerar(self._conversa(pedido))
        return MensagemAtiva(
            resposta.conteudo.strip() or self._persona.mensagem_fallback,
            resposta.modelo,
            resposta.tokens_entrada,
            resposta.tokens_saida,
        )

    async def preencher_ganchos(
        self, pedido: PedidoMensagemAtiva, template: TemplateLogico
    ) -> GanchosTemplate:
        """Fora da janela: só as variáveis "gancho" do template (o core valida cada uma)."""
        ganchos = template.ganchos
        instrucao = "\n".join(
            [
                "[Template — uso interno] Esta mensagem sai por um TEMPLATE aprovado, com "
                "texto fixo. Você NÃO escreve a mensagem: só o valor de cada variável abaixo.",
                f"Texto fixo do template: {template.texto_referencia}",
                "Regras de cada valor: uma linha só, sem quebra de linha, sem tabulação, sem "
                "markdown, sem repetir o que o texto fixo já diz, e nada que não esteja na "
                "conversa ou nos dados acima.",
                *(
                    f"- {v.nome} (até {v.max_caracteres or MAX_CARACTERES_VARIAVEL} "
                    f"caracteres): {v.descricao}"
                    for v in ganchos
                ),
            ]
        )
        schema = {
            "type": "object",
            "properties": {v.nome: {"type": "string"} for v in ganchos},
            "required": [v.nome for v in ganchos],
            "additionalProperties": False,
        }
        resposta = await self._llm.gerar_estruturado(
            [*self._conversa(pedido), MensagemLLM(PapelLLM.SISTEMA, instrucao)],
            schema,
            "ganchos_template",
        )
        return GanchosTemplate(
            {k: v for k, v in resposta.dados.items() if isinstance(v, str)},
            resposta.modelo,
            resposta.tokens_entrada,
            resposta.tokens_saida,
        )
