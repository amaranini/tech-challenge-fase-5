# ADR 013 — Observabilidade com Langfuse: transversal nos adapters, port genérico no core

- **Status:** Aceita
- **Data:** 2026-10-10
- **Relacionados:** [ADR 004](004-agente-llm-memoria.md), [ADR 005](005-grafo-qualificacao-multiagente.md),
  [ADR 012](012-canal-whatsapp-templates.md)

## Contexto

Precisamos ver, por conversa, o que o agente fez em cada turno: por quais nós passou,
quantas chamadas ao LLM, com que modelo, tokens, custo e latência — e chegar a isso a
partir do dashboard. Os dados passam por telefone, e-mail e CPF dos leads, que não podem
sair do processo. O core não pode depender de fornecedor (ADR 001/004).

## Decisão

1. **Port genérico no core** (`application/ports/observabilidade.py`): `RastreioPort` com
   `rastrear(ContextoRastro)` (um rastro por unidade de trabalho), `etapa(nome)` e
   `geracao(nome, modelo, entrada)`, todos gerenciadores de contexto. `RastreioNulo` é o
   padrão: sem configuração, nada muda. O core não importa Langfuse nem OpenTelemetry
   (contrato 5 do import-linter).
2. **Onde se abre o rastro (core):** `ProcessarTurno` (um por turno, com o lote agregado
   como entrada e a resposta como saída), `ExecutarFollowUps` (só quando a mensagem vai
   mesmo sair — cancelados/adiados não geram ruído) e `GerarResumoHandoff` (só quando o
   resumo é redigido). O `trace_id` e o link vão para `mensagem.metadados["trace"]` e para
   o evento `ResumoHandoffGerado`; a API expõe `trace_id`/`trace_url` em cada mensagem.
3. **Transversal nos adapters:**
   - `LLMObservado` embrulha qualquer `LLMPort`: cada chamada vira uma **geração** (modelo,
     entrada, saída, tokens; latência pelos tempos). O bootstrap embrulha o LLM de cada
     nó (`roteador`, `extracao`, `agente`) e os de `resumo` e `followup`.
   - O `AgenteQualificador` embrulha cada nó do grafo numa **etapa** (ferramentas como
     `tool`) com o resumo do que o nó decidiu. Como o LLM não é um modelo LangChain, um
   callback do LangChain não aninharia as gerações dentro dos nós; o embrulho dos nós dá o
     mesmo resultado (span por nó) com as gerações dentro, pelo contexto de execução.
   - `RastreioLangfuse` (SDK v4, sobre OpenTelemetry) é o único módulo que importa o SDK.
4. **Atributos do trace:** `session_id` = id do lead (agrupa turnos, follow-ups e resumos da
   conversa), `user_id` = hash do lead, tags `canal:`, `atendimento:`, `intencao:`,
   `vertical:` e metadata com as mesmas dimensões, a versão do prompt da persona, as
   versões dos prompts do core e, no fim, a intenção final do turno.
5. **PII:** a `mask` do cliente Langfuse passa toda entrada, saída e metadata pelo
   mascaramento do domínio (`domain/pii.py`: telefone — inclusive URL-encoded —, e-mail,
   CPF). Metadata propagada também é mascarada antes de sair. `user_id` não é o telefone.
6. **Custo:** o Langfuse calcula pelo modelo e pelos tokens (tabela de preços dele, que
   cobre os modelos da OpenAI); não mantemos tabela própria.
7. **Ligado só com as chaves** `LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY` (`LANGFUSE_BASE_URL`
   escolhe a região). O link dos traces é montado no start (uma chamada HTTP, fora do
   event loop). API e worker enviam o pendente ao encerrar.

## Consequências

- Trocar de fornecedor (OpenTelemetry puro, Datadog…) = outro adapter do `RastreioPort`.
- Os testes do adapter usam o próprio SDK com um exportador OpenTelemetry em memória:
  nada sai para a rede, e dá para conferir aninhamento, atributos e mascaramento.
- O SDK reaproveita o cliente por chave pública (estado global); em testes, uma chave por
  instância.
- Falha do Langfuse não derruba o fluxo: o SDK exporta em segundo plano.
