# Roadmap para produção

O projeto foi construído para **evoluir trocando adapters, nunca reescrevendo o núcleo**:
cada dependência externa está atrás de um port do core, e cada regra de negócio do
segmento está na vertical. Este roadmap lista o que muda para ir de POC a produção, em
ordem de prioridade, e onde cada mudança encaixa.

## 1. Adapters a trocar (o core não muda)

| Port | Hoje (POC) | Produção | O que muda |
|---|---|---|---|
| `CanalMensagemPort` + webhook | WhatsApp **Meta Cloud API** já em uso (e Twilio como alternativa), com o número da conta de demonstração | **número próprio** na conta oficial da empresa, verificação do negócio, display name aprovado | só configuração; webhook, assinatura, idempotência e janela de 24 h já estão prontos |
| Templates (`WHATSAPP_TEMPLATES`) | textos gerados em `docs/whatsapp-templates.md`, **não aprovados** — fora da janela de 24 h a Lia não envia (`EnvioTemplateIndisponivel`) | submeter cada `TemplateLogico` à Meta (categoria utility/marketing), mapear os IDs aprovados | só configuração; o validador de variáveis e o fallback já existem |
| `AgendaPort` | agenda mock em Postgres (grade gerada, ~1 em 4 ocupado) | **Google Calendar** ou **Outlook** (agenda real de cada corretor; free/busy + criação do evento com convite) | novo adapter; a regra "reserva só após o sim" e a idempotência continuam no core |
| `CRMPort` | `crm_registros` + log JSON Lines | **HubSpot** (upsert de contato/negócio pelo id externo, resumo como nota, etapa do funil pelos eventos) | novo adapter |
| `AgendadorTurnoPort` | debounce **in-process** (asyncio) ⇒ API com 1 réplica | **Redis** (chave por lead com expiração deslizante + fila de turnos) ou **Postgres** (tabela de turnos com `SKIP LOCKED`, como os follow-ups) | novo adapter + um consumidor; o advisory lock por lead continua garantindo um turno por vez |
| `LimitadorTaxaPort` | token bucket em memória | **Redis** (INCR + EXPIRE ou script de token bucket), atrás de um WAF/CDN na borda | novo adapter |
| `PublicadorEventosPort` | asyncio no mesmo processo (resumo fora do turno) | **outbox** transacional (eventos gravados com o turno) + fila (SQS/PubSub/Redis Streams) para resumo, CRM e notificações | novo adapter + worker; nenhum evento se perde em restart |
| `LLMPort` | OpenAI `gpt-4.1-mini` em todos os nós | modelo por nó (nano no roteador), cache de prompt, segundo provedor como fallback; acordo de retenção zero | configuração + adapter de fallback |
| `RastreioPort` | Langfuse Cloud | Langfuse self-hosted (dados na própria infraestrutura) ou o plano com retenção/região adequadas | configuração |

## 2. Escala horizontal

- **API:** com o agendador e o rate limit fora do processo (item 1), a API vira
  stateless e escala em réplicas atrás do load balancer. Hoje a limitação está
  documentada: **1 réplica** ([ADR 006](adr/006-processamento-assincrono-debounce.md)).
- **Turnos:** consumidores de turno separados da API (mesma imagem, outro comando), como já
  acontece com o worker de follow-up. O advisory lock por lead evita turnos concorrentes.
- **Worker de follow-up:** já escala (fila com `SKIP LOCKED`).
- **Banco:** pool de conexões (PgBouncer), réplica de leitura para o dashboard e as
  consultas de qualidade, backups com PITR. Particionar `lead_eventos` e `mensagens` por
  mês quando o volume pedir.
- **Busca:** o índice HNSW atende dezenas de milhares de imóveis; acima disso, um índice
  dedicado (Qdrant/OpenSearch) entra pelo `IndiceImoveisPort` sem mudar o caso de uso.

## 3. Multi-tenant (várias imobiliárias)

- **Tenant como conceito do core:** `tenant_id` em lead, conversa, eventos, catálogo,
  agenda e responsáveis; todas as consultas filtradas por tenant (Row Level Security no
  Postgres como segunda barreira).
- **Configuração por tenant:** persona (nome, tom, prompt versionado), canal (número de
  WhatsApp, templates aprovados), horário de atendimento, cadência de follow-up,
  responsáveis e regras de atribuição. Hoje isso está no `.env` e na vertical.
- **Roteamento de entrada:** o número de WhatsApp de destino (ou o domínio do widget web)
  identifica o tenant no webhook.
- **Custos e qualidade por tenant:** a página de Qualidade já fatia por versão; acrescentar
  o tenant como dimensão.

## 4. Segunda vertical

A arquitetura já separa o SDR genérico do negócio ([ADR 003](adr/003-core-multi-segmento.md)).
Uma segunda vertical (ex.: concessionária de veículos, clínica, academia) é um pacote
novo em `src/sdr/verticals/<segmento>/` implementando `VerticalPack`:

- catálogo (veículos, planos…) atrás do `CatalogoPort`, com a ferramenta de busca;
- persona e prompts versionados; intenções com schema de ficha, prioridade de campos,
  prompt do especialista e próxima ação;
- regras de scoring e critério de qualificado; regra de atribuição e tipos de
  agendamento; cadências e templates; template do resumo;
- `VERTICAL=<segmento>` no bootstrap; roteiros próprios em `evals/cenarios/`.

O core não muda, e o teste `test_core_agnostico` impede que vocabulário de segmento vaze
para ele. **Decisão consciente (YAGNI):** não existe "motor de configuração genérico"; a
segunda vertical é código, revisado e testado. Se surgirem várias verticais parecidas,
aí sim vale extrair configuração.

## 5. Segurança e conformidade

- **Identidade:** login do dashboard por IdP (SSO/OIDC) com usuários nominais e papéis
  (corretor, gestor, admin); API com tokens por usuário e escopos no lugar da chave
  única; trilha de auditoria de quem viu e quem alterou.
- **Chat web público:** identificador de sessão assinado (hoje é o `lead_id` escolhido
  pelo cliente).
- **LGPD:** exclusão também no Langfuse (API de deleção) e no CRM real; política de
  retenção (anonimizar conversas inativas há N meses); registro de consentimento no
  primeiro contato; DPA com o provedor de LLM.
- **Guardrails:** acompanhar a taxa de falso positivo e de falso negativo da detecção de
  injeção pela página de Qualidade; ampliar a heurística com casos reais.
- Riscos residuais atuais: [seguranca.md](seguranca.md#riscos-residuais-aceitos-na-poc).

## 6. Operação

- Deploy contínuo com o CI como gate (liberar o app GitHub da Railway no repositório;
  "Wait for CI" exige que todos os check suites concluam).
- Ambientes separados (staging com dados sintéticos e o eval rodando a cada mudança de
  prompt ou modelo; produção com aprovação).
- Alertas: SLA da fila estourado, taxa de `EnvioTemplateIndisponivel`, erros de entrega,
  custo diário do LLM acima do orçamento, latência p95.

## 7. Qualidade da IA em produção

Hoje existem duas camadas, ambas na página **🧪 Qualidade da IA** do dashboard:

- **Eval offline:** 15 roteiros (`evals/`) rodados antes de publicar uma versão, com
  métricas objetivas, LLM-as-judge e relatório (`evals/relatorio.md`). O runner grava o
  resumo na API (`POST /qualidade/offline`).
- **Métricas online:** das conversas reais, por versão do agente (persona · modelo ·
  prompts do core): pedido de humano, opt-out, abandono e onde o lead parou,
  qualificação (turnos e tempo), respostas bloqueadas, tentativas de injeção, latência,
  custo por conversa e por agendamento (`GET /qualidade/online`).

Próximos passos, em ordem:

### 1. LLM-as-judge por amostragem nas conversas reais
- **O quê:** a mesma rubrica do eval offline (`evals/rubrica_juiz_v1.md`), aplicada a
  uma amostra das conversas encerradas (ex.: 5–10% por dia e 100% das que tiveram
  handoff, opt-out, abandono ou incidente de guardrail).
- **Onde:** assíncrono, num laço novo do **worker** (mesma fila com SKIP LOCKED dos
  follow-ups), nunca no turno. Um caso de uso `AvaliarConversa` no core, com o juiz
  atrás de um port (`JuizConversaPort`); o modelo é configurável e diferente do da Lia.
- **PII:** a transcrição vai ao juiz já mascarada (`mascarar_pii`), como no Langfuse; a
  nota guarda só a referência da conversa.
- **Saída:** evento `ConversaAvaliada` (notas por critério + justificativa curta), score
  no Langfuse na sessão do lead, e na página Qualidade a média por versão ao lado das
  métricas online.
- **Revisão humana:** conversas com nota < 3 em qualquer critério entram numa fila
  "para revisar" na página (com o link do rastro). O revisor confirma ou corrige a nota,
  e essas correções calibram o juiz (comparação periódica entre juiz e humanos).

### 2. Feedback do corretor sobre o resumo e a ficha
- **Já existe (Dia 5):** botão "👍 útil / 👎 não útil" no resumo (Fila e detalhe do lead),
  gravado como evento `ResumoAvaliado`, com a taxa de útil exibida na página Qualidade.
- **Próximo:** o corretor marca o campo da ficha ou o trecho do resumo que estava errado
  e informa o valor certo (evento `CorrecaoDoResponsavel`).
- **Fecha o ciclo:** cada correção vira um **cenário candidato do eval offline**: a
  conversa anonimizada vira o roteiro (falas do lead) e o valor corrigido vira o estado
  esperado. Um revisor aprova e o JSON entra em `evals/cenarios/`. Assim, os erros reais
  viram testes de regressão, e as métricas de extração deixam de ficar saturadas em
  100% com roteiros fáceis.

### 3. Também na fila de qualidade
- Checagem de "falso vazio" na busca: quando a Lia diz que não há opções, rodar a mesma
  ficha pela busca de regras (o eval mostrou que a ancoragem não vê omissões).
- Rodar cada roteiro do eval 3–5 vezes e reportar taxas (o LLM varia entre execuções).
- Relógio fixo no ambiente de eval, para os roteiros de agenda serem determinísticos.
