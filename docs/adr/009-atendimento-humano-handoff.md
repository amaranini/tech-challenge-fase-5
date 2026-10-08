# ADR 009 — Atendimento humano: máquina de estados, gate no grafo e compare-and-set

- **Status:** Aceita
- **Data:** 2026-10-07
- **Relacionados:** [ADR 005](005-grafo-qualificacao-multiagente.md),
  [ADR 006](006-processamento-assincrono-debounce.md),
  [ADR 008](008-resumo-handoff-crm.md)

## Contexto

O lead pode pedir para falar com uma pessoa, ou a conversa pode precisar de uma (por
frustração, assunto fora do escopo ou negociação). Esse handoff ao vivo, com fila, é
diferente de agendar uma visita (ADR 007). Enquanto um humano atende, a IA não pode falar.
Na fila, o lead precisa saber que está esperando e poder voltar para a assistente. Tudo
isso convive com o turno assíncrono (ADR 006): o corretor pode assumir *enquanto* o grafo
está pensando.

## Decisão

1. **Máquina de estados no domínio do core** (`domain/atendimento.py`). Ela é pura, tem uma
   tabela explícita de transições e levanta `TransicaoInvalidaError` fora dela. Os estados:
   - `ATENDIMENTO_IA → CONFIRMANDO_HANDOFF → AGUARDANDO_HUMANO → ATENDIMENTO_HUMANO →
     ATENDIMENTO_IA`;
   - `AGUARDANDO_HUMANO → CONFIRMANDO_RETORNO_IA → ATENDIMENTO_IA` (cancela a fila);
   - `CONFIRMANDO_HANDOFF → ATENDIMENTO_IA` (o lead recusou);
   - `CONFIRMANDO_RETORNO_IA → AGUARDANDO_HUMANO` (o lead desistiu de voltar).

   Além da tabela, cada operação valida a própria origem. Exemplo: `devolver` só a partir de
   `ATENDIMENTO_HUMANO`. Um teste pegou que a tabela sozinha permitia "devolver" de
   `CONFIRMANDO_HANDOFF`. A resposta ambígua faz a pergunta de novo uma única vez; na
   segunda, o lead permanece onde estava (IA ou fila).
2. **"Falar com humano" é do core.** O roteador (prompt `roteador_v2`) devolve, além da
   intenção da vertical, o campo `atendimento_humano`: `nao`, `pedido_explicito`,
   `frustracao`, `fora_do_escopo` ou `negociacao`. É um campo separado, não mais uma
   intenção, porque o lead pode querer comprar E falar com alguém, e a ficha não deve
   mudar por isso.
3. **Gate no início do grafo**, antes do roteador:
   - `ATENDIMENTO_HUMANO` → silêncio, sem nenhuma chamada ao LLM;
   - `AGUARDANDO_HUMANO` → nó `espera`, que classifica se o lead quer voltar para a
     assistente;
   - `CONFIRMANDO_*` → nó de confirmação, que classifica sim, não ou ambígua.

   Esses nós desembocam em `responder_atendimento`: persona + `atendimento_v1` + um bloco
   interno com o que aconteceu, o horário de atendimento e quando a equipe retorna. A
   mensagem nunca promete tempo de espera e varia o texto entre as respostas.
4. **O grafo decide, o turno aplica.** O grafo devolve uma `AcaoAtendimento` e prevê o novo
   estado com o domínio, para redigir a resposta. Antes de enviar, o `ProcessarTurno`
   **recheca** o estado: se ele mudou durante o turno (por exemplo, um humano assumiu), a
   resposta é descartada. Depois, o turno aplica a ação pelo caso de uso correspondente
   (`SolicitarHandoff`, `ConfirmarHandoff`, `SolicitarRetornoIA`, `ConfirmarRetornoIA`), com
   **compare-and-set**: `UPDATE ... WHERE atendimento_estado = esperado`. O `salvar` do lead
   nunca grava o atendimento, então um turno da IA não sobrescreve o humano.
5. **A equipe opera por HTTP** (`AssumirAtendimento`, `DevolverAtendimento`,
   `EnviarMensagemResponsavel`):
   - `GET /atendimentos/fila` (por tempo de espera) e `GET /atendimentos?estado=...`;
   - `POST /atendimentos/{id}/assumir|devolver|mensagens`;
   - uma transição inválida devolve 409.

   A mensagem do responsável sai pelo `CanalMensagemPort` do canal do lead.
6. **Autor em cada mensagem:** `lead | assistente | responsavel`. A migration 0009 renomeia
   "agente" para "assistente". As falas da equipe entram no contexto da IA como nota de
   sistema ("[Rafael, da equipe, escreveu ao lead]: ..."), então a Lia retoma sabendo o que
   foi dito.
7. **O horário de atendimento é da operação** (`ATENDIMENTO_DIAS`, `ATENDIMENTO_FAIXAS`,
   `FUSO_OPERACAO`), representado pelo value object `HorarioAtendimento`, que tem
   `aberto`, `proxima_abertura` e `descrever`.
8. **Resumo:** `HandoffConfirmado` gera o resumo de handoff. As mensagens do lead na fila
   (`MensagemDuranteEspera`) atualizam o resumo e entram literais numa seção anexada.

## Consequências

- Um turno de atendimento custa uma classificação (modelo de extração) e uma redação.
  Durante o atendimento humano, não há nenhuma chamada ao LLM.
- As mensagens do lead enquanto o humano atende continuam passando pelo debounce e pelo
  turno (só para registrar). É uma simplificação: um canal real entregaria direto ao
  atendente.
- A tela "Fila" do Streamlit é a ferramenta da equipe na POC. Em produção, ela vira o
  inbox do CRM ou a ferramenta de atendimento. Os casos de uso não mudam.
