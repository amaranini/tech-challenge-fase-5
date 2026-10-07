# ADR 004 — Agente conversacional: LLM por port, LangGraph como orquestrador, memória no banco

- **Status:** Aceita
- **Data:** 2026-10-06
- **Relacionados:** [ADR 001](001-arquitetura-hexagonal.md),
  [ADR 003](003-core-multi-segmento.md)

## Contexto

A Etapa C coloca a Lia no ar. Os requisitos são:

- conversa com vários turnos e contexto;
- retomar a conversa pelo `lead_id`;
- tom de WhatsApp, com uma pergunta por vez;
- **nunca citar imóvel fora da base**.

O LLM mudou de Anthropic para **OpenAI** por decisão da PO, o que confirma na prática que o
provedor precisa ser trocável. O agente fica no core (ADR 003), então não pode saber nada de
imóvel.

## Decisão

1. **O core fala com o LLM pelo `LLMPort`**, um contrato próprio com mensagens, tool calling
   e tokens, que não é um modelo LangChain.
   - O adapter atual é `LLMOpenAI` (Chat Completions), escolhido por `LLM_PROVIDER=openai`,
     com o modelo em `LLM_MODELO` (`gpt-4.1-mini` por padrão).
   - Falhas de infraestrutura ou credencial viram `LLMIndisponivelError`, que a borda HTTP
     devolve como 503.
2. **O LangGraph só orquestra.** O `AgenteLangGraph` implementa o
   `AgenteConversacionalPort` com o grafo `agente ⇄ ferramentas`. O nó `agente` chama o
   `LLMPort`, e o nó `ferramentas` executa as tools da vertical, que chamam PORTS. Há um teto
   de passos (`AGENTE_MAX_PASSOS`); ao atingi-lo, o LLM é obrigado a responder em texto.
3. **A memória fica no nosso banco, não no LangGraph** (o agente não usa checkpointer).
   - Lead, Conversa e Mensagem são do core.
   - A cada turno, o caso de uso carrega as últimas `CONVERSA_JANELA_HISTORICO` mensagens e
     as entrega ao agente.
   - Cada mensagem da Lia guarda em `metadados` (JSONB) os itens citados (código e resumo),
     as tools chamadas, a versão do prompt, o modelo e os tokens.
   - Ao remontar o contexto, o agente recebe um lembrete com os itens que já apresentou, o
     que permite responder a "o primeiro" sem buscar de novo.
4. **Há uma entrada única por canal.** `ProcessarMensagemRecebida(MensagemRecebida)` grava a
   mensagem do lead **antes** de chamar o LLM, para que nada se perca se ele falhar. O chat
   web usa `canal=web`, e o WhatsApp (Dia 4) usará o mesmo caso de uso.
5. **Proteção contra imóvel inventado**, em três camadas:
   - **O prompt** proíbe inventar e exige citar o código.
   - **A tool** só devolve itens do catálogo, e o `CatalogoPort` rejeita filtros
     desconhecidos.
   - **O caso de uso confere os códigos citados** (regex fornecida pela persona da
     vertical): o que não veio da tool é procurado no catálogo (`obter`). Se algum código
     não existir, o caso de uso pede **uma** correção ao agente. Se o erro persistir, a
     resposta vira a mensagem de fallback da persona e o caso fica registrado em
     `metadados`.
6. **A vertical fornece persona e tools pelo `VerticalPack`.** O contrato ganhou
   `VerticalMontada.persona` (prompt versionado em `persona/prompts/<versao>.md`, escolhido
   por `IMOBILIARIO_VERSAO_PROMPT`) e `ferramentas`. A tool `buscar_imoveis` é a
   `FerramentaBuscarCatalogo`, genérica do core, que recebe a definição (nome, descrição e
   JSON Schema) da vertical.

## Consequências

- **Trocar de provedor** (Anthropic, Azure OpenAI, um modelo local) exige um novo adapter
  de `LLMPort` e uma linha em `PROVEDORES_LLM`, sem mexer no agente nem no núcleo.
- **Trocar de framework de agente** exige um novo adapter de `AgenteConversacionalPort`, e
  memória e guardas continuam iguais.
- **O histórico é auditável e consultável por SQL** (dashboard do Dia 4, eval do Dia 5),
  porque cada resposta tem a versão do prompt, o modelo e os tokens.
- **Custos e limitações:**
  - um código inventado custa uma chamada extra ao LLM;
  - a janela de histórico é fixa, sem sumarização;
  - conversas longas perdem o começo; quando isso importar, a evolução é um resumo
    persistido da conversa.
- **Ainda não há streaming:** o chat espera a resposta inteira, o que é aceitável com
  mensagens curtas.

## Alternativas consideradas

- **ChatOpenAI do LangChain dentro do grafo:** é menos código, mas acopla o agente a um SDK e
  a um formato de mensagens de terceiros. Trocar de provedor passaria a ser trocar de
  integração do LangChain, não um adapter nosso.
- **Checkpointer do LangGraph (Postgres) como memória:** criaria duas fontes de verdade para
  a conversa e um formato opaco para dashboard e eval.
- **`create_react_agent` pronto:** menos controle sobre o teto de passos, a coleta dos itens
  consultados e a conversão de mensagens.
