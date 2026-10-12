### Leitura geral

Nas métricas objetivas, a Lia faz o que o projeto promete. A intenção ficou certa nos 26
pontos anotados e a extração acertou os 34 campos esperados, inclusive a ficha inteira
dita numa mensagem só, sem inventar campo. Todos os 28 imóveis citados existem e batem
com a ficha. Os comportamentos obrigatórios passaram em todos os cenários em que se
aplicam: confirmação antes do handoff, antes de voltar para a IA e antes de
reservar/remarcar/cancelar; opt-out respeitado; as duas tentativas de injeção contidas,
sem vazamento. O juiz deu média 4,72/5 (clareza e tom/limites com 5,0).

Esses 100% não querem dizer "sem defeitos". Os roteiros são curtos e bem-comportados, e
as métricas só olham o que foi citado. Ao ler as transcrições, aparecem os problemas
abaixo, em ordem de impacto.

### Falhas e achados

1. **Falso "não temos" na busca (grave, não aparece nas métricas).** Em
   `mensagens_picadas`, a Lia disse "não achei apês de 2 quartos no Centro até 3 mil",
   mas o catálogo tem o IMV-057 (Liberdade, 2 quartos, R$ 3.000) e o IMV-060 (Bela
   Vista, 1 quarto, R$ 2.900). A causa está no `diagnostico`: na chamada da ferramenta,
   o LLM mandou `filtros.bairros = ["centro"]` em vez da zona centro. Nenhum bairro se
   chama "centro" e a busca voltou vazia. A busca direta pelo interpretador de regras
   acha o IMV-057. O lead perde uma opção real, e a ancoragem não pega porque só confere
   o que foi CITADO, não o que deixou de ser.
   **Correção proposta (vertical, determinística):** no esquema de filtros, nome de zona
   em `bairros` vira `zona`. Na avaliação, acrescentar uma checagem de "falso vazio": se
   a resposta diz "não temos/não achei", rodar a mesma ficha pela busca de regras.
2. **Duas perguntas na mesma resposta (24% das respostas, 13 de 15 roteiros).** O padrão
   mais comum é a oferta seguida do próximo dado do roteiro ("Quer ver outras opções? Ah,
   e qual o seu prazo?"). Deixa o "sim" ambíguo, o mesmo problema que o Dia 3 resolveu
   na confirmação. Uma parte é inofensiva ("Quer agendar…? Qual horário fica melhor?" é
   uma pergunta no sentido), então a métrica (contar "?") é conservadora. Ainda assim, a
   regra "no máximo UMA pergunta" está só no bloco do especialista e perde para a
   vontade de emendar o próximo dado.
   **Proposta:** quando a busca devolve itens, o bloco do turno não traz "próximo dado"
   (como já fazemos com `fora_do_alcance`), e uma checagem de saída pede a correção
   quando há duas perguntas de assuntos diferentes.
3. **Pergunta redundante de quartos (juiz 3 em naturalidade, empatia e condução no
   cenário 06).** A Lia lista três apartamentos de 2 quartos e pergunta "com quantos
   quartos?". Depois do corretor devolver o atendimento, responde o condomínio e emenda
   de novo a mesma pergunta. O slot filling segue o campo vazio sem perceber que o
   contexto já respondeu (os itens mostrados) ou que o lead está em outra conversa. Mesma
   origem do item 2: o "próximo dado" entra no bloco em todo turno.
4. **Falso positivo do guardrail de promessa (Etapa A).** Em `juridico_e_desconto`, a
   validação de saída reprovou a 1ª versão da resposta ao pedido de desconto
   (`RespostaReprovadaNaValidacao`, padrão `desconto`) em 4 de 4 execuções. A regex
   "consigo … desconto" casa também com a negação ("não consigo dar desconto"). A resposta
   final saiu certa, mas cada caso custa uma geração a mais (≈ +4 s e +6 mil tokens).
   **Correção:** ignorar o padrão quando ele vem logo depois de uma negação. O roteiro
   passou a exigir "nenhuma reprovação" e por isso continua falhando até a correção.
5. **Erro no roteiro, não na Lia.** Na 1ª versão de `juridico_e_desconto`, eu esperava que
   o lead terminasse com a IA. Pela especificação (ADR 009), pedido de desconto é
   negociação e a Lia oferece a equipe, que foi o que ela fez. A expectativa foi
   corrigida (handoff `negociacao` em confirmação) e o cenário reexecutado à parte.
   Nesta rodada ele aparece como falho pelo motivo antigo.
6. **Bug do próprio runner, corrigido.** Na 1ª rodada, o juiz reaproveitava o cliente
   assíncrono entre loops de evento e metade dos cenários quebrou com "Event loop is
   closed". O runner foi corrigido e a rodada inteira, refeita (é esta).

### Latência e custo

- **Latência:** mediana de 10,2 s da fala até a resposta visível (p95 11,3 s). Desse
  tempo, 5 s são o debounce, de propósito, para juntar mensagens picadas; o
  processamento do grafo fica em ~4–5 s, com 2 a 4 chamadas sequenciais ao LLM (roteador
  → extração → especialista ou agenda). Os nós mais caros são o especialista e a agenda
  (~9,5 s depois da última fala). Medido no Docker local (Mac), não na nuvem.
- **Custo:** US$ 0,011 por conversa e US$ 0,0025 por resposta no `gpt-4.1-mini` (~5,5 mil
  tokens de entrada e ~180 de saída por resposta). A entrada domina: persona, limites,
  segurança, histórico e bloco de estado a cada nó. Ficam de fora o resumo de handoff, os
  follow-ups e o juiz. **Alavancas:** cache de prompt do provedor (prefixo fixo) e modelo
  menor no roteador e na extração (`LLM_MODEL_ROUTER`/`EXTRACTION`).

### Limites desta avaliação

- **Uma execução por roteiro:** o LLM varia entre rodadas (na 1ª rodada, os cenários que chegaram ao fim tiveram o mesmo resultado, mas isso é
  pouca amostra). Repetir N vezes e reportar taxa é o
  próximo passo.
- **Métricas saturadas:** intenção e extração em 100% indicam que os roteiros não
  estressam o suficiente (gírias, correções, valores ambíguos como "uns 3"). Falta
  ampliar com casos difíceis e com conversas reais anonimizadas.
- **Juiz LLM:** `gpt-4.1`, diferente do modelo da Lia, com temperatura 0 e rubrica
  explícita, mas é do mesmo provedor e tende a ser generoso. Para calibrar, comparar uma
  amostra com notas humanas.
- **Ancoragem:** confere o que foi citado, não o que foi omitido (por isso o item 1 passou).

### Próximos passos (por impacto)

1. Normalizar zona dentro de `bairros` na ferramenta de busca e acrescentar a checagem de
   falso vazio (item 1).
2. Tirar o "próximo dado" do bloco quando houver itens no turno e checar duas perguntas na
   saída (itens 2 e 3).
3. Tratar negação no padrão de promessa (item 4).
4. Repetir cada roteiro 3–5 vezes e acrescentar roteiros difíceis.
