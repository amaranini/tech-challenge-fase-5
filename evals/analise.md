### Leitura geral

Este relatório é a rodada final, feita depois de três correções que a avaliação apontou
(abaixo, antes × depois). Nas métricas objetivas, a Lia faz o que o projeto promete. A
intenção ficou certa nos 26 pontos anotados e a extração acertou os 34 campos esperados,
inclusive a ficha inteira dita numa mensagem só, sem inventar campo. Todos os 29 imóveis
citados existem e batem com a ficha. Os comportamentos obrigatórios passaram em todos os
cenários em que se aplicam: confirmação antes do handoff, antes de voltar para a IA e
antes de reservar/remarcar/cancelar; opt-out respeitado; as duas tentativas de injeção
contidas, sem vazamento. O juiz deu média 4,80/5.

A única reprovação desta rodada (`pedido_fora_da_base_vira_visita`) é da checagem do
roteiro, não da Lia (item 5). Corrigida a checagem, o cenário passa.

### Antes × depois das correções

| | Rodada de base | Rodada final |
|---|---|---|
| Respostas com duas perguntas (todas) | 16/66 (24%) | 10/66 (15%) |
| … só no especialista (onde estava o problema) | 9/31 | 2/31 |
| "Não temos" falso no centro (`mensagens_picadas`) | sim: escondeu o IMV-057 | não: mostra o IMV-057 e o IMV-060 |
| Reprovação falsa por "desconto" negado | 4 de 4 execuções | 0 |
| Juiz: condução · média geral | 4,67 · 4,72 | 4,93 · 4,80 |
| Latência percebida (mediana) | 10,2 s | 9,2 s |
| Custo por conversa | US$ 0,0110 | US$ 0,0109 |

### Correções desta etapa (achadas pela avaliação)

1. **"Não temos" falso na busca (grave, e nenhuma métrica pegava).** A Lia dizia "não
   achei apês de 2 quartos no Centro até 3 mil" com o IMV-057 (Liberdade, 2 quartos,
   R$ 3.000) na base. Pelo `diagnostico` da resposta, o LLM tinha mandado
   `filtros.bairros = ["centro"]`. Nenhum bairro se chama "centro" e a busca voltava
   vazia. **Corrigido na vertical:** nome de zona em `bairros` ("centro", "zona sul",
   "região oeste") vira filtro de zona (`esquema_filtros.py`).
2. **Duas perguntas na mesma resposta.** O padrão era a oferta seguida do próximo dado do
   roteiro ("Quer ver outras opções? Ah, e qual o seu prazo?"), que deixa o "sim" do lead
   ambíguo. **Corrigido no core:** quando a busca termina no especialista ou na
   descoberta, entra uma nota de sistema logo depois do resultado ("termine com UMA única
   pergunta, sobre o resultado; o próximo dado fica para depois"). É o mesmo padrão que
   funcionou nos dias anteriores, sinal estruturado no turno em vez de mais instrução no
   prompt. No especialista caiu de 9/31 para 2/31. O que resta é quase todo da agenda
   ("Quer agendar…? Qual horário fica melhor?"), que é uma pergunta só no sentido. A
   métrica (contar "?") é conservadora de propósito.
3. **Falso positivo do guardrail de promessa (Etapa A).** "Não consigo dar desconto"
   casava com o padrão de desconto, e uma resposta correta era regenerada à toa (≈ +4 s e
   +6 mil tokens). **Corrigido no domínio:** padrões de promessa negados logo antes ("não",
   "nem", "nunca", "sem", na mesma oração) são ignorados.

### Falhas encontradas na própria avaliação (corrigidas)

4. **Agenda mock poluída pelas rodadas.** Cada execução reservava horários reais e nunca
   cancelava. Depois de algumas rodadas, a terça de manhã da especialista se esgotou e o
   cenário de investimento passou a falhar ("só tenho 12h ou 13h"; a Lia, certa, não
   reservou sem um horário concreto). **Corrigido:** o runner anonimiza cada lead ao fim
   (rota LGPD, que cancela agendamento e follow-ups e libera o horário). Os leads de teste
   antigos foram limpos.
5. **Checagens de roteiro mal formuladas.** No `juridico_e_desconto`, eu esperava que o
   lead terminasse com a IA. Pela especificação (ADR 009), pedido de desconto é
   negociação e a Lia oferece a equipe, que foi o que ela fez; a expectativa foi
   corrigida. No `pedido_fora_da_base_vira_visita`, a checagem proibia a palavra
   "quartos" (para a Lia não voltar a PERGUNTAR quantos quartos) e passou a reprovar a
   simples descrição do imóvel ("2 quartos, suíte…"); agora proíbe "quantos quartos". Os
   dois foram reexecutados à parte e passam.
6. **Bug do runner.** Na primeira rodada, o juiz reaproveitava o cliente assíncrono entre
   loops de evento ("Event loop is closed") e metade dos cenários quebrou. Corrigido; as
   rodadas usadas aqui são posteriores.

### O que continua em aberto

- **Pergunta redundante depois do atendimento humano** (juiz 3 em naturalidade e empatia
  no cenário 06). Quando o corretor devolve a conversa, a Lia responde a dúvida e emenda
  de novo "com quantos quartos?". O slot filling retoma o campo vazio sem considerar que
  o lead acabou de falar com uma pessoa e já viu as opções. Proposta: depois de
  `AtendimentoHumanoEncerrado`, o bloco do 1º turno não traz "próximo dado".
- **A métrica de ancoragem não vê omissões.** Ela confere o que foi citado, não o que
  deixou de ser (por isso o item 1 passou nela). Proposta: checagem de "falso vazio",
  rodar a ficha pela busca de regras quando a Lia disser que não há opções.

### Latência e custo

- **Latência:** mediana de 9,2 s da fala até a resposta visível (p95 11,3 s). Desse
  tempo, 5 s são o debounce, de propósito, para juntar mensagens picadas; o grafo leva
  ~4 s, com 2 a 4 chamadas sequenciais ao LLM (roteador → extração → especialista ou
  agenda). Medido no Docker local (Mac).
- **Custo:** US$ 0,011 por conversa e US$ 0,0025 por resposta no `gpt-4.1-mini` (~5,5 mil
  tokens de entrada e ~175 de saída por resposta). A entrada domina: persona, limites,
  segurança, histórico e bloco de estado em cada nó. Ficam de fora o resumo de handoff,
  os follow-ups e o juiz. **Alavancas:** cache de prompt do provedor (o prefixo é fixo) e
  modelo menor no roteador e na extração (`LLM_MODEL_ROUTER`/`EXTRACTION`).

### Limites desta avaliação

- **Uma execução por roteiro:** o LLM varia entre rodadas. As quatro rodadas desta etapa
  foram consistentes nas métricas objetivas (fora as falhas já explicadas acima), mas isso
  é pouca amostra; repetir 3–5 vezes
  e reportar taxas é o próximo passo.
- **Métricas saturadas:** intenção e extração em 100% indicam que os roteiros não
  estressam o suficiente (gírias, correções, valores ambíguos como "uns 3"). Falta
  ampliar com casos difíceis e conversas reais anonimizadas.
- **Juiz LLM:** `gpt-4.1`, diferente do modelo da Lia, temperatura 0 e rubrica explícita,
  mas do mesmo provedor e tende a ser generoso (muitas notas 5). Calibrar com uma amostra
  de notas humanas.
- **Roteiros dependentes do relógio:** os de agenda pedem "terça de manhã" e dependem dos
  horários livres no momento da execução; um relógio fixo na API de avaliação tornaria a
  agenda determinística.
