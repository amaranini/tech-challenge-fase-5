# ADR 011 — Follow-up automático: fila com SKIP LOCKED, worker sem regras, cadência da vertical

- **Status:** Aceita
- **Data:** 2026-10-08
- **Relacionados:** [ADR 006](006-processamento-assincrono-debounce.md),
  [ADR 007](007-agendamento-agenda-mock.md), [ADR 009](009-atendimento-humano-handoff.md)

## Contexto

O lead para de responder no meio da conversa. O SDR precisa retomar no tempo certo
(D+1, D+3, encerramento em D+7) e com contexto, trazendo algo de valor, sem pressão. Há
três exigências:

- não insistir quando não deve: o lead respondeu, está com um humano, já agendou, pediu
  para parar, ou está fora do horário;
- lembrar o agendamento na véspera;
- alertar a equipe quando a fila do humano passa do SLA.

Tudo isso roda fora da conversa, num processo separado e seguro para várias réplicas.

## Decisão

1. **Fila em Postgres:** `followups_agendados`, com tipos `retomada`,
   `lembrete_agendamento` e `sla_handoff`. O consumo usa `SELECT ... FOR UPDATE SKIP
   LOCKED` numa transação curta que marca o item como `processando`. A geração da mensagem
   (LLM) não segura lock, e vários workers nunca pegam o mesmo item. Um item `processando`
   há mais de 10 min (worker caiu) volta à fila.
2. **Worker sem regra:** `python -m sdr.worker`, serviço `worker` no compose, com a mesma
   imagem da API. É só um laço que chama `ExecutarFollowUps` via bootstrap.
3. **Regras no domínio do core** (`domain/followup.py`):
   - situação do lead → cadência;
   - elegibilidade (opt-out, conversa encerrada, fora da IA, lead respondeu, agendamento
     futuro, fora do horário = adiar);
   - janela de 24h → `requer_template`;
   - SLA do handoff contado só em **tempo útil** do horário de atendimento.
4. **Programação, pelo caso de uso `ProgramarFollowUps`:**
   - **cada resposta do assistente** recomeça a cadência (a etapa 1 conta dessa
     resposta);
   - **mensagem do lead** (`ReceberMensagem`) cancela os pendentes e, se ele respondia a
     um follow-up, gera `LeadReengajado`;
   - **opt-out:** o roteador detecta, o nó `opt_out` confirma com educação e o turno chama
     `registrar_opt_out`;
   - **eventos assinados:** agendamento criado ou remarcado gera o lembrete 24h antes;
     cancelamento derruba o lembrete; `HandoffConfirmado` programa a checagem de SLA;
     sair da fila a cancela.
5. **O conteúdo das mensagens é da vertical.**
   - **Cadência** (`VerticalPack.cadencias_followup`), por situação: descoberta,
     qualificação ou qualificado. Cada etapa traz o **objetivo** da mensagem (o que dizer)
     e o **template** aprovado no canal, usado fora da janela de 24h.
   - **Lembrete** (`VerticalPack.lembrete_agendamento`), com objetivo e template. Sem ele,
     o core não programa lembretes.
   - **"Algo de valor"** (`consulta_followup`): ficha → busca no catálogo de um item
     compatível **ainda não apresentado**.

   O core só garante as regras de toda mensagem ativa: retomar do ponto, sem pressão,
   nada inventado. A unidade (`FOLLOWUP_UNIDADE=dias|minutos`) é config da operação.
6. **Mensagem gerada pelo LLM** (`RedatorMensagemAtivaPort`, prompt `mensagem_ativa_v1`).
   Usa a voz da persona, a conversa inteira, a ficha e o item novo, retoma do ponto onde
   parou e não pressiona. A última etapa é o encerramento com a porta aberta. Vale a mesma
   regra "nada fora da base" do turno: código inexistente pede uma correção e, se
   persistir, a mensagem sai sem o item.
7. **Envio pelo `CanalMensagemPort`.** Fora da janela de 24h, a mensagem sai por
   `enviar_template` e é marcada `requer_template` (relevante para o WhatsApp no Dia 4).
   Detalhado no [ADR 012](012-canal-whatsapp-templates.md): a decisão passou para o caso
   de uso `EntregarMensagem` e o template virou `TemplateLogico` com variáveis validadas.
8. **Demo:** `POST /demo/leads/{id}/simular-inatividade` antecipa a próxima retomada para
   agora, sem esperar o expediente, e o worker a envia na próxima varredura. O painel tem o
   botão "⏩ Simular inatividade". Lead com opt-out responde 409.
9. **Eventos:** `FollowUpAgendado`, `FollowUpEnviado`, `LeadEncerradoPorInatividade`,
   `LeadReengajado`, `LeadOptOut` e `HandoffSLAExcedido` (este aparece no dashboard do
   Dia 4).

## Consequências

- O worker monta o container inteiro (inclusive o embedding, para buscar o item novo). A
  imagem é a mesma da API.
- `opt_out_em` é gravado por um `UPDATE` próprio. O `salvar` do lead não o apaga, seguindo
  o mesmo padrão do atendimento (ADR 009).
- A cadência recomeça a cada resposta do assistente. Por isso, um lead que conversa todo
  dia nunca recebe follow-up, que é o desejado.
