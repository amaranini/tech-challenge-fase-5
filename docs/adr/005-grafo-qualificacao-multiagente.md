# ADR 005 — Grafo genérico de qualificação (roteador, extração, scoring, especialistas)

- **Status:** Aceita
- **Data:** 2026-10-06
- **Relacionados:** [ADR 003](003-core-multi-segmento.md) (core × vertical),
  [ADR 004](004-agente-llm-memoria.md) (agente e memória)

## Contexto

No Dia 2, a Lia passa a **qualificar** o lead. Ela precisa:

- entender a intenção (compra, aluguel, investimento), inclusive quando o lead muda de
  ideia no meio da conversa;
- preencher uma ficha estruturada sem parecer formulário;
- pontuar o lead (quente, morno, frio) e marcar a próxima ação.

Tudo isso precisa servir a outros segmentos (ADR 003): o core não pode conhecer as
intenções nem os campos.

## Decisão

1. **O grafo do core tem um caminho fixo e conteúdo plugável:**
   `roteador → extração → scoring → especialista ⇄ ferramentas`, mais o caminho
   `roteador → descoberta ⇄ ferramentas` enquanto a intenção é desconhecida. Os itens
   abaixo vêm do `VerticalPack`:
   - as intenções, com descrição, schema da ficha, prioridade de campos, prompt do
     especialista e próxima ação;
   - as regras de scoring e o critério de qualificado;
   - o prompt de descoberta.

   A intenção `indefinida` é do core.
2. **A extração vem antes do especialista.** Isso inverte a ordem do pedido original, por
   decisão da PO. Assim o especialista já enxerga a ficha atualizada pelo turno atual e não
   pergunta o que o lead acabou de dizer.
3. **As regras ficam no domínio, não no grafo.** O agregado `Qualificacao`
   (`core/domain/qualificacao.py`) concentra as regras universais, como funções puras que
   devolvem `(novo estado, eventos)`:
   - **troca de intenção:** limiar de confiança; `indefinida` mantém a intenção atual; a
     nova ficha herda campos de mesmo nome;
   - **merge incremental:** nulo nunca apaga; um campo preenchido só muda com
     `campos_corrigidos` e só some com `campos_removidos`; listas acumulam;
   - **campos faltantes**, em ordem de prioridade;
   - **`LeadQualificado`** só na transição de não qualificado para qualificado.

   Os nós do LangGraph apenas chamam essas funções e o LLM.
4. **Há uma ficha por intenção**, guardada em JSONB como `{"compra": {...}, "aluguel": {...}}`.
   Trocar de intenção não apaga nada, e voltar retoma a ficha anterior. Intenção, score,
   classificação, motivos, próxima ação e `qualificado_em` também ganham **colunas** em
   `leads`, para o dashboard filtrar sem ler JSON.
5. **A saída estruturada do LLM** vem de `LLMPort.gerar_estruturado`, que no adapter OpenAI
   é `json_schema` com `strict=False`. A garantia de aderência vem da **nossa** validação:
   cada campo é validado isoladamente pelo schema da vertical (`SchemaFicha.validar`), e um
   valor inválido é descartado sem derrubar o turno.
6. **Cada nó tem seu modelo:** `LLM_MODEL_ROUTER`, `LLM_MODEL_EXTRACTION` e
   `LLM_MODEL_AGENT`, com `LLM_MODELO` como padrão. Roteador e extração rodam com
   temperatura 0.
7. **Quando sugerir itens do catálogo** (acrescentado na Etapa B). A
   `IntencaoVertical` declara `campos_para_sugerir`. Com esses campos preenchidos, o bloco
   de estado manda o especialista buscar e apresentar até 3 opções antes da pergunta.
   **Depois de qualificado**, o bloco para de pedir campos, e a única pergunta conduz à
   `proxima_acao`. Nos testes com LLM real, a instrução só no prompt da vertical não
   bastava: a orientação de "uma pergunta sobre o próximo dado" prevalecia.
8. **Slot filling humanizado.** Um bloco interno de estado entregue ao especialista
   informa o próximo campo, com descrição, e a instrução de no máximo uma pergunta,
   aproveitando o que o lead já disse e respondendo antes às dúvidas dele. Os demais
   campos faltantes vão como contexto, para não serem perguntados agora.
9. **Eventos de domínio** são gravados em `lead_eventos` (tipo, payload JSONB, timestamp):
   - os do pedido: `LeadCriado`, `IntencaoIdentificada`, `IntencaoAlterada`,
     `CampoQualificacaoPreenchido`, `ScoreAlterado`, `LeadQualificado`;
   - dois acrescentados: `CampoQualificacaoCorrigido` e `CampoQualificacaoRemovido`.

## Consequências

- **Uma vertical nova** declara intenções, schemas e regras, e o fluxo inteiro já funciona.
  Os testes do core provam isso com uma vertical fake ("academia") sem Pydantic.
- **A trilha de eventos** permite auditar a qualificação e, no Dia 4, alimentar dashboard e
  métricas sem reprocessar conversas.
- **Custo:** de 3 a 4 chamadas ao LLM por turno, contra 1 ou 2 antes. Roteador e extração
  são chamadas curtas e podem usar um modelo menor.
- **Persistência sem transação única.** Mensagem, lead e eventos são gravados em transações
  separadas. Uma falha no meio pode deixar a mensagem gravada sem os eventos.
  Evolução: Unit of Work.
- **A herança entre intenções é por nome de campo.** As verticais devem usar o mesmo nome
  para o mesmo conceito (ex.: `regiao`).

## Alternativas consideradas

- **Um único agente com "qualifique o lead" no prompt:** é mais simples, mas sem ficha
  confiável, sem eventos e sem como testar as regras.
- **Extração via tool call do especialista:** o modelo decide quando extrair e
  frequentemente não extrai. Ter um nó dedicado garante a extração a cada turno.
- **`strict=True` no structured output:** exige todos os campos obrigatórios e anuláveis,
  o que complica os schemas da vertical. A validação por campo cobre o risco.
