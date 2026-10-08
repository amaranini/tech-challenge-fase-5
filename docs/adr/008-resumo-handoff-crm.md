# ADR 008 — Resumo para o responsável (fora do turno, ancorado) e CRMPort

- **Status:** Aceita
- **Data:** 2026-10-07
- **Relacionados:** [ADR 006](006-processamento-assincrono-debounce.md) (turnos assíncronos),
  [ADR 007](007-agendamento-agenda-mock.md) (agendamento)

## Contexto

Quando o lead qualifica, agenda (ou, na Etapa C, aceita falar com um humano), a pessoa da
equipe que vai atendê-lo precisa de um resumo. No resumo entram:

- perfil e necessidades;
- score e motivos;
- imóveis vistos e reação a cada um;
- objeções;
- perguntas em aberto;
- próximo passo;
- trechos-chave.

Três exigências:

1. O resumo **não pode atrasar** a resposta da Lia.
2. Ele **não pode inventar**: um corretor que chega na visita com uma informação falsa
   perde a venda.
3. Ele precisa chegar ao **CRM** do time comercial.

## Decisão

1. **Reação a eventos, fora do turno.** O `ProcessarTurno` grava os eventos, envia a
   resposta e só então chama `PublicadorEventosPort.publicar(eventos)`.
   - O adapter da POC (asyncio in-process) entrega em segundo plano, um lote por vez por
     lead e na ordem.
   - Em produção, o adapter troca por outbox + fila. Os eventos já estão em `lead_eventos`,
     então um reprocessador repõe o que se perder num reinício.
2. **`GerarResumoHandoff` (core) assina os eventos.**
   - Gatilhos iniciais: `LeadQualificado` e `AgendamentoCriado` (`HandoffConfirmado` entra
     na Etapa C). Eles geram uma versão.
   - Gatilhos de atualização: ficha alterada, intenção trocada, agendamento remarcado ou
     cancelado. Eles só regeneram se já existe um resumo.
   - Uma nova versão só é gerada quando a **impressão digital** dos fatos muda: ficha,
     score, agendamento, itens citados e mensagens do lead. Sem mudança, o LLM nem é
     chamado.
3. **Template da vertical, ancoragem do core.** A vertical declara um `TemplateResumo`
   (seções com título, tipo e instrução). O core preenche as seções conforme o tipo:
   - **ficha, score e agendamento** saem do estado do lead, nunca do LLM. O que falta na
     ficha aparece como "não informado".
   - **texto**: redigido pelo LLM; se vier vazio, "não informado".
   - **lista** (objeções, perguntas): cada item exige uma *evidência*, isto é, uma fala do
     lead copiada palavra por palavra. Item sem evidência é descartado.
   - **itens do catálogo**: só os que foram citados na conversa (metadados das mensagens).
     A reação do lead exige evidência; sem ela, fica "não informado". Um item citado que o
     LLM esqueceu entra assim mesmo, com reação "não informado".
   - **trechos**: só falas literais do lead.

   **Texto livre passa por uma checagem de afirmações.** Com o LLM real, a ancoragem
   descartou a reação inventada "o IMV-041 chamou atenção", mas a mesma invenção voltou no
   "próximo passo" ("levar o IMV-041, que chamou mais atenção do lead"). Por isso:
   - um verificador independente (segunda chamada, `resumo_checagem_v1`) quebra cada texto
     em frases e classifica a fonte de cada uma: conversa (exige evidência literal), dados
     ou recomendação pura;
   - o domínio (`podar_textos`) mantém só as frases sustentadas, citadas sem reescrita;
   - sem checagem, o texto vira "não informado". Na dúvida, não se afirma.

   O que a ancoragem e a checagem descartam fica em `descartados`, para auditoria. O LLM fica atrás de
   `RedatorResumoPort`, com o prompt versionado em `agent/prompts/resumo_v1.md` e o modelo
   em `LLM_MODEL_SUMMARY`.
4. **Resumos versionados** na tabela `resumos_handoff`, com `(lead_id, versao)` único,
   o gatilho, a versão do template, o modelo e os tokens.
5. **`CRMPort`** cria ou atualiza o lead e anexa o resumo. O **mock** grava em
   `crm_registros` (um registro por lead, upsert, `crm_id` estável) e em um log JSON Lines
   (`CRM_MOCK_LOG`). **Em produção, o adapter troca por HubSpot ou outro CRM:** upsert de
   contato ou negócio pelo id externo e o resumo como nota.
6. **Exposição:**
   - `GET /leads/{id}/resumo` devolve a última versão (ou `?versao=N`) e a lista de versões;
   - `GET /agendamentos` aceita filtros por status e data;
   - `GET /leads/{id}` passa a trazer o agendamento ativo;
   - o painel do Streamlit mostra o agendamento e o resumo.

## Consequências

- A Lia responde no mesmo tempo de antes. O resumo aparece alguns segundos depois. O
  painel e os testes fazem polling, e o evento `ResumoHandoffGerado` registra cada versão.
- A ancoragem é mecânica: ela garante a *origem* do que foi dito, não a interpretação.
  "Achou caro", apoiado em "achei caro", passa; uma conclusão sem fala do lead não passa.
- Nos textos livres, a garantia depende de um segundo LLM (o verificador), então é
  probabilística. Um resumo custa 2 chamadas, ambas fora do turno. A qualidade do
  verificador entra no eval do Dia 5.
- Uma entrega em andamento se perde se o processo cair (POC, 1 réplica). Os eventos
  persistidos permitem repor quando a fila entrar.
