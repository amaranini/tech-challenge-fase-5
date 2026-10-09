# ADR 012 — Canal WhatsApp (Twilio) e templates: a janela de 24h decidida no core

- **Status:** Aceita
- **Data:** 2026-10-08
- **Relacionados:** [ADR 006](006-processamento-assincrono-debounce.md),
  [ADR 009](009-atendimento-humano-handoff.md), [ADR 011](011-followup-worker.md)

## Contexto

O lead passa a conversar pelo WhatsApp (Twilio Sandbox na POC; número aprovado em
produção). O canal traz quatro exigências que o chat web não tinha:

- o provedor chama um **webhook** que precisa ser autenticado (assinatura) e que ele
  **reenvia** em caso de timeout;
- toda resposta — da IA, da equipe (tela Fila), do follow-up e do lembrete — precisa sair
  pelo **canal de origem do lead**;
- passadas **24h da última mensagem do lead**, a empresa só pode escrever com **template
  aprovado pela Meta**, com variáveis numeradas e regras rígidas de conteúdo;
- áudio, imagem e documento chegam, mas o assistente ainda só entende texto.

Até o Dia 3, o envio estava espalhado (turno, equipe e follow-up chamavam o canal direto),
e o template era só um nome: o follow-up mandava o texto inteiro como variável, o que a
Meta recusaria.

## Decisão

1. **Entrada pelo mesmo caso de uso.** `POST /webhooks/whatsapp/twilio`:
   - valida o `X-Twilio-Signature` (HMAC-SHA1) com a URL **pública** (`PUBLIC_BASE_URL`):
     atrás de túnel/proxy a URL interna difere da que o Twilio assinou; inválida ⇒ 403;
   - normaliza para `MensagemRecebida` (canal `whatsapp`, remetente em E.164, nome do
     perfil, anexos, `id_externo` = MessageSid) e chama o **mesmo `ReceberMensagem`** do
     chat web: grava PENDENTE, agenda o turno (debounce) e responde 200 com TwiML vazio na
     hora;
   - **idempotência por MessageSid** no core: `ReceberMensagem` confere o id externo e o
     banco tem índice único parcial (`mensagens.id_externo`) contra a corrida de reenvios;
   - o lead é identificado por (canal, telefone) e criado se for novo.
2. **A decisão texto livre × template é do core** — caso de uso `EntregarMensagem`, por
   onde passam as quatro saídas:
   - janela contada da **última mensagem do lead** (nossas mensagens não renovam);
   - dentro ⇒ `enviar_texto`; fora ⇒ `enviar_template` com o `TemplateLogico` que o
     chamador indicar;
   - fora da janela **sem template** (ou com template não mapeado no provedor) ⇒ **nada sai
     como texto livre**: a mensagem fica `nao_enviada` (fora da memória do agente), com
     entrega `falhou`, e o evento `EnvioTemplateIndisponivel` avisa a operação;
   - em duas fases (`preparar` → quem chama persiste → `transmitir`), para manter a ordem
     de gravação do turno (ADR 006 e 009).
3. **`CanalMensagemPort` só executa:** `enviar_texto(lead, mensagem)` e
   `enviar_template(lead, mensagem, nome_logico, variaveis_nomeadas)`, devolvendo os ids do
   provedor. O adapter Twilio converte a marcação para o padrão do WhatsApp (`*negrito*`),
   divide mensagens longas (1600 caracteres, quebrando em parágrafo → linha → frase →
   palavra) e converte variáveis nomeadas → numeradas (Content API: `ContentSid` +
   `ContentVariables`). Usa a API REST com `httpx` assíncrono: o SDK oficial é síncrono e
   travaria o event loop. Erros do provedor viram `FalhaEnvioError` (evento
   `MensagemNaoEntregue`); template não mapeado vira `TemplateIndisponivelError`.
4. **Templates em três camadas:**
   - **vertical** — `TemplateLogico` (nome lógico, categoria, texto de referência para a
     Meta, variáveis nomeadas com regra de preenchimento a partir da ficha/contexto, valor
     padrão e exemplo), um por etapa de follow-up, lembrete e resposta da equipe fora da
     janela (`VerticalMontada.template_resposta_responsavel`);
   - **operação** — `WHATSAPP_TEMPLATES` (JSON): nome lógico → template aprovado
     (`content_sid`, idioma, ordem das variáveis);
   - **core** — preenche, passa **toda** variável pelo validador (sem quebra de linha/tab,
     sem 4+ espaços, não vazia, tamanho máximo configurável) e cai no padrão se faltar
     dado ou o valor for recusado. O LLM só escreve as variáveis marcadas como **gancho**
     (saída estruturada), também validadas. O texto do template renderizado é o que fica
     gravado na mensagem: histórico, painel e preview na web mostram o que o lead recebeu.
   - `TemplateLogico` recusa, já na declaração, o que a Meta reprova: nome fora do padrão,
     texto que começa/termina em variável, variáveis adjacentes.
   - `docs/whatsapp-templates.md` é **gerado** (`python -m sdr.cli templates-doc`) e um
     teste confere que está em dia.
5. **Status de entrega:** cada parte enviada fica em `envios_canal` (id externo → mensagem).
   O callback `POST /webhooks/whatsapp/twilio/status` (mesma assinatura) atualiza a parte
   sem regredir (callbacks chegam fora de ordem) e agrega na mensagem (`entrega`: enviada,
   entregue, lida, falhou). Falha ⇒ `MensagemNaoEntregue`.
6. **Mídia:** registrada nos metadados da mensagem do lead. Lote só com anexo ⇒ o turno
   responde o `Persona.aviso_midia` da vertical, sem LLM (em atendimento humano, silêncio).
   Texto com anexo ⇒ o agente recebe o texto e uma nota de que veio um anexo que ele não
   abre.
7. **Lead público nas rotas:** web continua `lead-ana`; outros canais viram
   `whatsapp:+5511...`. A Fila, o painel e as rotas `/leads`, `/conversas`, `/atendimentos`
   e `/demo` aceitam os dois.
8. **PII nos logs:** `FiltroPII` nos handlers (inclusive o access log do uvicorn) mascara
   telefone (inclusive URL-encoded), e-mail e CPF. As regras ficam em `domain/pii.py`,
   reaproveitadas no Langfuse (Etapa B). `LeadCriado` não grava mais o remetente.

## Consequências

- Canal novo = adapter de entrada (webhook → `MensagemRecebida`) + adapter de saída
  (`CanalMensagemPort`). Turno, atendimento e follow-up não mudam.
- **Sandbox do Twilio:** dentro das 24h tudo funciona com texto livre. Fora delas, só
  saem templates aprovados — no Sandbox, apenas os de exemplo do Twilio. Até os templates
  da vertical serem aprovados num número próprio, mensagens ativas fora da janela ficam
  como não enviadas, com evento, o que é o comportamento correto.
- A resposta da equipe fora da janela não chega como texto: sai o template de retomada
  (convite para o lead responder). O texto original fica gravado (`envio.texto_original`)
  para a equipe reenviar quando o lead responder.
- A mesma pessoa no web e no WhatsApp são dois leads (identidade = canal + remetente).
  Unificar exige uma decisão de produto (vínculo por telefone/e-mail) e fica para depois.
- POC com 1 réplica da API: o webhook só grava e agenda; o turno roda no mesmo processo
  (ADR 006). Em produção, o webhook continua igual e o agendador vira fila.
