# ADR 017 — Qualidade da IA: métricas online por versão do agente + eval offline na API

- **Status:** Aceita
- **Data:** 2026-10-12
- **Relacionados:** [ADR 013](013-observabilidade-langfuse.md), [ADR 014](014-dashboard-consultas.md),
  [ADR 015](015-seguranca-guardrails.md) · avaliação em [evals/README.md](../../evals/README.md)

## Contexto

O eval offline (Dia 5C) mede a Lia em roteiros controlados, mas o relatório fica num
arquivo e não diz como as conversas REAIS estão indo nem se uma versão nova de prompt ou
de modelo melhorou ou piorou. A operação precisa ver os dois lados no dashboard, fatiados
pela versão do agente, com os detalhes técnicos a um clique (Langfuse).

## Decisão

1. **Versão do agente = persona (prompt da vertical) · modelo · prompts do core.** Cada
   resposta grava as três coisas nos metadados (`prompt_versao`, `modelo`,
   `agente.prompts`). Cada conversa conta na versão da sua 1ª resposta (coorte), o que
   permite comparar versões lado a lado.
2. **Métricas online como read model** (`core/application/consultas/qualidade.py`, SQL
   em `ConsultasQualidadeSql`), sobre `lead_eventos` e as mensagens, como o painel
   (ADR 014): pedido de humano, opt-out, abandono e "onde parou" (o que a última
   resposta pedia: campo da ficha, escolha de horário, descoberta), qualificação (turnos
   e tempo), respostas reprovadas/bloqueadas pela validação, tentativas de injeção,
   latência p50/p95 e custo por conversa e por agendamento. O preço por modelo é
   configuração (`CUSTO_LLM_POR_MILHAO`), aplicado no caso de uso.
3. **Eval offline gravado na API:** tabela `avaliacoes_offline` (JSONB: versão,
   métricas, contexto). O runner faz POST ao fim de cada rodada completa, também contra a
   nuvem; o `relatorio.md` continua sendo a peça completa.
4. **Feedback do responsável:** "útil / não útil" no resumo vira o evento
   `ResumoAvaliado` (comando `AvaliarResumo`) e entra nas métricas.
5. **Langfuse por link:** incidentes e exemplos levam ao rastro do turno e à sessão do
   lead (`<projeto>/sessions/<lead_id>`); o projeto sai da URL de qualquer rastro.

## Consequências

- Uma página nova (`web/paginas/qualidade.py`), só via API, com rotas `/qualidade/*` e
  `/leads/{id}/resumo/avaliacao` atrás da chave de admin.
- Conversas anteriores ao registro dos prompts do core aparecem como `core#antigo`.
- Abandono depende de uma janela (`abandono_horas`, padrão 24 h) e exclui conversas que
  terminaram bem (opt-out, agendamento ativo, atendimento humano).
- Próximos passos (juiz por amostragem nas conversas reais e correções do responsável
  virando cenários do eval) em `docs/roadmap-producao.md`.
