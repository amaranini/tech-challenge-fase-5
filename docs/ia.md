# A IA da Lia — como funciona e como foi avaliada

Este documento explica a parte de inteligência artificial do Agente SDR: quais modelos
são usados e onde, como os agentes se organizam, como a busca de imóveis (RAG) funciona,
como a ficha do lead é preenchida, como a conversa é lembrada, quais guardrails existem e
o que a avaliação mediu. A arquitetura geral está em [arquitetura.md](arquitetura.md).

**Princípio que atravessa tudo:** o LLM **interpreta e redige**; quem **decide** é código
determinístico. Intenção, campos da ficha e horários passam por regras do domínio; preço,
imóvel e horário só existem se vieram da base. Isso deixa o comportamento testável e
impede o LLM de inventar.

## 1. Modelos — por nó e por quê

| Onde | Variável | Padrão | Tarefa | Por que esse modelo |
|---|---|---|---|---|
| Roteador | `LLM_MODEL_ROUTER` | `gpt-4.1-mini` | classificar a última fala: intenção + confiança, pedido de humano, agendar, fora do alcance, opt-out, tentativa de injeção, fora do domínio | saída estruturada confiável e barata; candidato a `gpt-4.1-nano` (é o nó mais simples) |
| Extração | `LLM_MODEL_EXTRACTION` | `gpt-4.1-mini` | preencher a ficha da intenção a partir da conversa (JSON Schema da vertical) | precisa seguir schema e **não chutar**; o mini acertou 100% dos campos no eval |
| Especialistas, descoberta, agenda, atendimento | `LLM_MODEL_AGENT` | `gpt-4.1-mini` | redigir a resposta da Lia e chamar a ferramenta de busca | bom português, tool calling, baixa latência; o turno faz 2–4 chamadas em sequência |
| Resumo para o corretor | `LLM_MODEL_SUMMARY` | `gpt-4.1-mini` | redigir o resumo e verificar cada frase contra os fatos | roda fora do turno; dá para usar um modelo maior sem afetar a latência do lead |
| Juiz da avaliação | `EVAL_MODELO_JUIZ` | `gpt-4.1` | dar notas 1–5 na rubrica | mais forte e **diferente** do modelo da Lia, para reduzir o viés de autoavaliação |
| Embeddings | `EMBEDDING_MODELO` | `paraphrase-multilingual-MiniLM-L12-v2` (384 d) | vetor do imóvel e da consulta | local (ONNX, fastembed), multilíngue, sem custo por chamada e sem enviar dados a terceiros |

- **Um modelo por nó** é configuração, não código: o bootstrap cria um `LLMPort` por nó
  ([ADR 004](adr/004-agente-llm-memoria.md)).
- **Provedor:** `LLM_PROVIDER=openai`. Trocar de provedor é escrever outro adapter do
  `LLMPort`, sem mexer no grafo.
- **Temperatura:** 0,4 nas respostas e 0 no juiz. As classificações usam saída estruturada.

## 2. O grafo de agentes

O turno é um grafo LangGraph com nós especializados ([ADR 005](adr/005-grafo-qualificacao-multiagente.md)).
O LangGraph **só orquestra**: as regras ficam no domínio do core (merge da ficha, troca
de intenção, estados do atendimento, decisão de agenda) e na vertical (schema, scoring,
critério de qualificado). O diagrama está em [arquitetura.md §6](arquitetura.md#6-grafo-de-agentes-dentro-do-turno).

1. **Gate do atendimento.** Com uma pessoa da equipe atendendo, a IA fica em silêncio,
   sem chamar o LLM. Na fila ou em confirmação, um nó classifica a resposta: sim, não ou
   ambígua.
2. **Roteador.** Classifica a **última fala**, com a conversa recente como contexto. Os
   sinais de segurança e de pedido de humano valem só para a última fala: com o
   histórico no prompt, o modelo "herdava" um pedido ou uma tentativa já tratados (visto
   com o LLM real).
3. **Extração.** Preenche a ficha da intenção. O resultado passa pela validação da vertical
   (Pydantic) e pelo merge do domínio.
4. **Scoring.** Regras da vertical calculam score de 0 a 100, com motivos legíveis, e o
   critério de qualificado, que gera `LeadQualificado` e a `proxima_acao`.
5. **Especialista.** Um por intenção: compra, aluguel ou investimento. Responde com a
   persona e um **bloco de estado** gerado pelo core (ficha, próximo dado, se já pode
   sugerir imóveis, se já está qualificado). Faz **no máximo uma pergunta**.
6. **Descoberta.** Enquanto a intenção é indefinida, descobre o objetivo sem chutar.
7. **Agenda.** O LLM só **interpreta** a fala (escolher, remarcar, cancelar, preferência de
   dia e período). Os horários reais e a decisão vêm do domínio (`decidir`); a reserva só
   acontece depois do "sim" explícito.
8. **Redirecionar.** Para tentativa de injeção ou assunto alheio: responde no papel, sem
   ferramentas e sem tocar na ficha.
9. **Opt-out e atendimento.** Confirmam com educação e devolvem a **ação** (pedir handoff,
   voltar para a IA). O turno aplica a ação com compare-and-set.

**Lição aprendida, repetida em vários dias:** instrução só no prompt perde para o "roteiro"
de perguntas. O que funciona é um **sinal estruturado** (do roteador ou do turno) que
muda o bloco daquele turno. Exemplos: `fora_do_alcance` tira o "próximo dado"; depois de
uma busca, uma nota exige "uma pergunta, sobre o resultado" (isso derrubou as respostas
com duas perguntas de 9/31 para 2/31 no especialista).

## 3. RAG — busca híbrida de imóveis

O agente não "sabe" imóveis: ele chama a ferramenta `buscar_imoveis` e só pode citar o
que voltou dela ([ADR 002](adr/002-busca-hibrida-embeddings-locais.md)).

- **Híbrida, numa query só:** um `WHERE` com os filtros estruturados (finalidade, tipo,
  zona, bairro, preço, quartos, vagas, distância do metrô, pet, mobiliado) e
  `ORDER BY embedding <=> consulta` (cosseno, índice HNSW do pgvector).
- **Texto indexado:** título, tipo, bairro, zona, descrição, estação de metrô e
  comodidades, para que bairro e lazer também pesem na similaridade.
- **Filtros vindos do LLM:** como argumentos da tool (JSON Schema da vertical, validado
  com Pydantic). A busca direta (`POST /imoveis/busca`) usa um interpretador por regras
  (regex) para texto livre.
- **Correção que veio do eval:** o LLM às vezes mandava `bairros=["centro"]`. Nenhum
  bairro tem esse nome e a busca voltava vazia, então a Lia dizia "não temos" com o
  imóvel na base. Agora, nome de zona em `bairros` vira filtro de zona.
- **Anti-alucinação:** os códigos citados no texto (`IMV-123`) são conferidos no catálogo
  antes do envio. Código inexistente pede uma correção ao agente; se persistir, sai a
  resposta segura da persona.

## 4. Saída estruturada e ficha incremental

- **Saída estruturada:** roteador, extração, interpretação da agenda, classificação do
  atendimento, verificação do resumo e juiz usam `response_format: json_schema`. O schema
  da ficha é **da vertical** (Pydantic por intenção), exposto ao core pelo protocolo
  `SchemaFicha`.
- **Uma ficha por intenção**, em JSONB (`{"compra": {...}, "aluguel": {...}}`). Trocar de
  intenção não apaga nada, e a ficha nova herda `regiao` e `quartos`.
- **Regras de merge (domínio, agregado `Qualificacao`):**
  - valor nulo nunca apaga;
  - campo preenchido só muda com correção explícita do lead ("na verdade, até 900 mil");
  - só some com remoção explícita.

  Cada mudança vira evento (`CampoQualificacaoPreenchido`, `…Corrigido`, `…Removido`,
  `IntencaoAlterada`, `ScoreAlterado`, `LeadQualificado`).
- **Valor de outra intenção não vaza:** na troca de aluguel para compra, um aviso explícito
  impede que "4 mil por mês" vire preço de compra (havia um evento `CampoCorrigido`
  indevido antes disso).
- Regras de negócio da vertical: [qualificacao-imobiliaria.md](qualificacao-imobiliaria.md).

## 5. Memória conversacional

- **A memória é o banco**, não o LangGraph (sem checkpointer). A cada turno, o agente
  recebe as últimas `CONVERSA_JANELA_HISTORICO` mensagens (30), e roteador e extração leem
  as `EXTRACAO_JANELA_MENSAGENS` mais recentes (6).
- **O estado durável é estruturado:** ficha, score, intenção, negociação de agenda, estado
  do atendimento e itens já apresentados (anotados no histórico). O modelo não precisa
  "lembrar" de tudo pelo texto.
- **Mensagens pendentes do próprio turno** não entram no histórico: elas são a fala atual.
- **Persistente e multicanal:** a mesma conversa continua depois de dias, inclusive com
  falas da equipe (marcadas como "Equipe" no contexto).

## 6. Debounce — responder como gente

No WhatsApp a pessoa manda "oi" / "quero alugar" / "no centro" / "até 3 mil", em
mensagens separadas. Responder cada uma seria ruim e caro.

- `ReceberMensagem` só grava a mensagem como **pendente** e agenda o turno. A resposta
  nunca volta na requisição: a web recebe 202 e o webhook, 200.
- O **debounce por lead** espera `DEBOUNCE_SEGUNDOS` (5 s) de silêncio, reiniciando a cada
  mensagem, com teto de `DEBOUNCE_MAX_SEGUNDOS` (20 s).
- O `ProcessarTurno` pega o **advisory lock** do lead, agrega as pendentes e roda o grafo
  **uma vez**. Se chega mensagem nova antes do envio, descarta a resposta e reprocessa
  com tudo ([ADR 006](adr/006-processamento-assincrono-debounce.md)).
- **"Digitando…" no WhatsApp:** quando o debounce termina e o turno começa, a última
  mensagem do lote é marcada como lida e o lead vê os três pontinhos enquanto a Lia
  pensa (Cloud API; some quando a resposta sai ou em 25 s). Com uma pessoa da equipe
  atendendo, não aparece.
- **Custo:** os 5 s entram na latência percebida, de propósito. O eval mediu ~9 s da
  última fala até a resposta, dos quais ~4 s são o grafo.

## 7. Guardrails

Detalhes em [seguranca.md](seguranca.md); resumo do que é IA:

| Risco | Controle |
|---|---|
| Prompt injection ("ignore suas instruções…") | hierarquia de instruções em toda resposta (`seguranca_v1`: lead e ferramentas são DADOS); heurística no domínio + `tentativa_injecao` do roteador; nó `redirecionar`; evento sem a fala |
| Vazamento de dados de terceiros | estrutural: o agente só tem ferramentas de catálogo e só vê o próprio lead |
| Assunto alheio | `fora_do_dominio` ⇒ recusa gentil e volta ao atendimento |
| Item inventado | códigos conferidos no catálogo ⇒ regenera uma vez ⇒ resposta segura |
| Promessa de preço/desconto/garantia | padrões do core + do segmento na saída (negação ignorada) ⇒ regenera ⇒ resposta segura |
| Conselho jurídico/financeiro definitivo | regra na hierarquia; pergunta jurídica real ⇒ oferta de transferir para a equipe |
| Resumo com "fatos" inventados | dados vêm do estado; imóveis só os citados; evidência literal das falas do lead; verificador de frases |
| PII | CPF não vai ao LLM; telefone/e-mail/CPF mascarados em logs, Langfuse e exportações |

## 8. Observabilidade

Cada turno, follow-up e resumo vira **um rastro** no Langfuse ([ADR 013](adr/013-observabilidade-langfuse.md)):
uma etapa por nó do grafo e uma geração por chamada ao LLM (modelo, tokens, custo,
latência), com `session_id` = lead e PII mascarada. Cada resposta guarda o link do rastro.
A página **🧪 Qualidade da IA** liga as métricas às sessões e aos rastros.

## 9. Avaliação e resultados

Relatório completo: [evals/relatorio.md](../evals/relatorio.md) (como rodar em
[evals/README.md](../evals/README.md)).

- **Como:** 15 roteiros com o LLM real contra a API: os exemplos do enunciado, troca de
  intenção, investidor, handoff com confirmação e retorno, corretor assumindo, follow-up
  e reengajamento, opt-out, fora do escopo, injeção, ambíguo/indefinido, mensagens
  picadas, tudo numa mensagem, jurídico e desconto.
- **O que mede:** as métricas olham o **estado** que o sistema gravou, não o texto exato:
  intenção por ponto anotado, extração campo a campo (precisão e cobertura),
  comportamentos obrigatórios (pela ordem dos eventos e das falas), ancoragem dos
  imóveis citados, juiz 1–5 com rubrica explícita
  ([rubrica_juiz_v1.md](../evals/rubrica_juiz_v1.md)), latência e custo.

**Rodada final (gpt-4.1-mini na Lia, gpt-4.1 no juiz):**

| Métrica | Resultado |
|---|---|
| Cenários aprovados | 14/15 (a reprovação era de uma checagem do roteiro, corrigida e reexecutada) |
| Acurácia de intenção | 100% (26/26) |
| Extração — precisão · cobertura | 100% · 100% (34 campos) |
| Imóveis citados que existem · aderentes à ficha | 29/29 · 29/29 |
| Confirmação antes de handoff · retorno · reserva | 2/2 · 1/1 · 4/4 |
| Opt-out respeitado · injeção contida | 1/1 · 2/2 |
| Juiz (naturalidade, empatia, clareza, condução, tom e limites) | 4,40 · 4,67 · 5,00 · 4,93 · 5,00 (média 4,80) |
| Respostas com duas perguntas | 15% (era 24% antes das correções) |
| Latência percebida (mediana · p95) | 9,2 s · 11,3 s, incluindo 5 s de debounce |
| Custo do LLM da Lia por conversa | **US$ 0,011** (~R$ 0,06) · US$ 0,0025 por resposta |

**O que a avaliação mudou:** encontrou e motivou três correções (o falso "não temos" da
busca, duas perguntas por resposta, o falso positivo do guardrail de desconto) e dois
problemas da própria avaliação (agenda mock poluída pelas rodadas, checagens mal
escritas). O relatório tem a análise honesta e o antes × depois.

**Limites assumidos:**
- uma execução por roteiro;
- roteiros fáceis demais para intenção e extração (100%);
- o juiz é um LLM, sem calibração humana;
- a ancoragem não detecta omissões.

Os próximos passos estão no [roadmap](roadmap-producao.md#7-qualidade-da-ia-em-produção).

## 10. Custo médio por conversa

| Item | Valor |
|---|---|
| Tokens por resposta (média) | ~5,5 mil de entrada · ~175 de saída |
| Respostas por conversa nos roteiros | ~4,4 |
| Custo do LLM no turno, por conversa | US$ 0,011 |
| Fora dessa conta | resumo do corretor (1–2 chamadas por lead qualificado), follow-ups (1 chamada cada), embeddings (locais, custo zero) |

**Para onde vai o custo:** a entrada domina (persona, limites, segurança, histórico e
bloco de estado em cada nó). Alavancas:
- cache de prompt do provedor (o prefixo é fixo);
- `gpt-4.1-nano` no roteador;
- histórico resumido para conversas longas.

Os preços ficam em `CUSTO_LLM_POR_MILHAO` e a página de Qualidade calcula o custo por
conversa e por agendamento, por versão.
