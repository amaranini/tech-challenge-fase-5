# Roadmap para produção

> Documento vivo. Esta primeira versão registra a evolução da **qualidade da IA**; as
> trocas de adapters, multi-tenant, segunda vertical, templates e escala entram na
> documentação final (Dia 5, Etapa D).

## Qualidade da IA em produção

Hoje existem duas camadas, ambas na página **🧪 Qualidade da IA** do dashboard:

- **Eval offline:** 15 roteiros (`evals/`) rodados antes de publicar uma versão, com
  métricas objetivas, LLM-as-judge e relatório (`evals/relatorio.md`). O runner grava o
  resumo na API (`POST /qualidade/offline`).
- **Métricas online:** das conversas reais, por versão do agente (persona · modelo ·
  prompts do core): pedido de humano, opt-out, abandono e onde o lead parou,
  qualificação (turnos e tempo), respostas bloqueadas, tentativas de injeção, latência,
  custo por conversa e por agendamento (`GET /qualidade/online`).

Próximos passos, em ordem:

### 1. LLM-as-judge por amostragem nas conversas reais
- **O quê:** a mesma rubrica do eval offline (`evals/rubrica_juiz_v1.md`), aplicada a
  uma amostra das conversas encerradas (ex.: 5–10% por dia e 100% das que tiveram
  handoff, opt-out, abandono ou incidente de guardrail).
- **Onde:** assíncrono, num laço novo do **worker** (mesma fila com SKIP LOCKED dos
  follow-ups), nunca no turno. Um caso de uso `AvaliarConversa` no core, com o juiz
  atrás de um port (`JuizConversaPort`); o modelo é configurável e diferente do da Lia.
- **PII:** a transcrição vai ao juiz já mascarada (`mascarar_pii`), como no Langfuse; a
  nota guarda só a referência da conversa.
- **Saída:** evento `ConversaAvaliada` (notas por critério + justificativa curta), score
  no Langfuse na sessão do lead, e na página Qualidade a média por versão ao lado das
  métricas online.
- **Revisão humana:** conversas com nota < 3 em qualquer critério entram numa fila
  "para revisar" na página (com o link do rastro). O revisor confirma ou corrige a nota,
  e essas correções calibram o juiz (comparação periódica entre juiz e humanos).

### 2. Feedback do corretor sobre o resumo e a ficha
- **Já existe (Dia 5):** botão "👍 útil / 👎 não útil" no resumo (Fila e detalhe do lead),
  gravado como evento `ResumoAvaliado`, com a taxa de útil exibida na página Qualidade.
- **Próximo:** o corretor marca o campo da ficha ou o trecho do resumo que estava errado
  e informa o valor certo (evento `CorrecaoDoResponsavel`).
- **Fecha o ciclo:** cada correção vira um **cenário candidato do eval offline**: a
  conversa anonimizada vira o roteiro (falas do lead) e o valor corrigido vira o estado
  esperado. Um revisor aprova e o JSON entra em `evals/cenarios/`. Assim, os erros reais
  viram testes de regressão, e as métricas de extração deixam de ficar saturadas em
  100% com roteiros fáceis.

### 3. Também na fila de qualidade
- Checagem de "falso vazio" na busca: quando a Lia diz que não há opções, rodar a mesma
  ficha pela busca de regras (o eval mostrou que a ancoragem não vê omissões).
- Rodar cada roteiro do eval 3–5 vezes e reportar taxas (o LLM varia entre execuções).
- Relógio fixo no ambiente de eval, para os roteiros de agenda serem determinísticos.
