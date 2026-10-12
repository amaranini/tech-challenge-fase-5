# Agente SDR Conversacional (vertical imobiliária) — Tech Challenge FIAP Fase 5

Agente de pré-vendas (SDR) imobiliário com IA generativa: a **Lia** atende leads, entende o que
procuram, sugere imóveis reais da base e encaminha para um corretor.

Core de SDR genérico + verticais de negócio (hoje: imobiliária), ambos hexagonais — veja
[docs/arquitetura.md](docs/arquitetura.md) e [docs/adr/](docs/adr/). A vertical ativa é
escolhida por `VERTICAL` no `.env`.

## Pré-requisitos

- [uv](https://docs.astral.sh/uv/) (instala o Python 3.12 automaticamente)
- Docker + Docker Compose

## Rodando

```bash
cp .env.example .env   # preencha OPENAI_API_KEY
docker compose up -d --build --wait
make seed        # carrega o catálogo da vertical (60 imóveis fictícios) + embeddings
make busca q="apê 2 quartos zona sul até 800 mil perto do metrô"
```

A primeira build baixa o modelo de embeddings (~220 MB) para dentro da imagem.

| Serviço | URL |
|---|---|
| API (FastAPI) | http://localhost:8000 — docs em `/docs` |
| Health check | http://localhost:8000/health |
| Web (Streamlit) — chat com a Lia | http://localhost:8501 |
| Postgres + pgvector | `localhost:5433` (usuário/senha/db: `sdr`) |

### Busca de imóveis

`POST /imoveis/busca` faz busca híbrida: filtros estruturados (inferidos do texto e/ou
explícitos em `filtros`) + similaridade semântica. Veja o schema em `/docs` e o
[ADR 002](docs/adr/002-busca-hibrida-embeddings-locais.md).

```json
{"texto": "apê 2 quartos zona sul até 800 mil perto do metrô", "filtros": {"aceita_pet": true}, "limite": 5}
```

### Conversa com a Lia

No Streamlit, crie ou escolha um `lead_id` na barra lateral e converse. Reabrir o mesmo
`lead_id` continua a conversa de onde parou. Pela API:

```bash
curl -s localhost:8000/conversas/mensagens -H 'content-type: application/json' \
  -d '{"lead_id": "lead-ana", "texto": "Oi! Procuro um apê de 2 quartos na zona sul"}'
# → 202: a Lia espera você parar de digitar (DEBOUNCE_SEGUNDOS) e responde ao conjunto
curl -s 'localhost:8000/conversas/lead-ana/mensagens'   # histórico + "processando"
```

`make e2e` roda o aceite com o LLM real (memória, retomada e nenhum imóvel inventado).
Detalhes em [ADR 004](docs/adr/004-agente-llm-memoria.md).

### Qualificação do lead

Ao lado do chat, o Streamlit mostra um **painel de qualificação** atualizado em tempo real:
intenção (compra/aluguel/investimento), ficha sendo preenchida (✅ preenchido · ⬜ faltando),
score 0–100 com a classificação (🔥 quente · 🌤️ morno · 🧊 frio) e os motivos, a próxima
ação quando o lead é qualificado e a trilha de eventos. Regras de negócio em
[docs/qualificacao-imobiliaria.md](docs/qualificacao-imobiliaria.md).

```bash
curl -s localhost:8000/leads/lead-ana | python3 -m json.tool
# intencao, ficha (atual), fichas (todas), campos_faltantes, score, classificacao,
# score_motivos, proxima_acao, qualificado_em e eventos (lead_eventos, em ordem); 404 se não existir
```

`make llm` roda os cenários de qualificação com o LLM real contra a API no ar
(compra → `agendar_visita`, investimento → `encaminhar_especialista` e troca
aluguel → compra). Os roteiros (falas do lead + estado final esperado) ficam em
[evals/cenarios/](evals/cenarios/) e serão reaproveitados no eval do Dia 5.

A API aplica as migrations (`alembic upgrade head`) ao iniciar.

### WhatsApp

A Lia atende também pelo WhatsApp: mesmas regras, mesmo turno assíncrono e mesma Fila.
Fora da janela de 24h, só saem templates aprovados ([ADR 012](docs/adr/012-canal-whatsapp-templates.md)).
O provedor é escolhido em `WHATSAPP_PROVEDOR`: `meta` (Cloud API oficial — o número de
teste é gratuito) ou `twilio` (conta trial só envia por template; precisa de upgrade).

**Túnel** para a API local (os provedores precisam de uma URL pública). Recomendado: ngrok
com o domínio fixo gratuito da conta — se o túnel cair, a URL continua a mesma e o webhook
não precisa ser reconfigurado:

```bash
ngrok config add-authtoken <authtoken>              # uma vez (painel do ngrok)
ngrok http --url=<seu-dominio>.ngrok-free.dev 8000  # Domains → domínio fixo gratuito
```

(`cloudflared tunnel --url http://localhost:8000` também serve, mas a URL muda a cada vez.)

#### Meta (WhatsApp Cloud API) — recomendado

1. Em [developers.facebook.com](https://developers.facebook.com), *Meus apps → Criar app*,
   caso de uso **WhatsApp**. Em *Configurações do app → Básico*, copie a **Chave secreta
   do app** (App Secret).
2. **Número:** o número de teste (+1 555…) é gratuito, mas a Meta **não entrega** para
   celular brasileiro a partir de número estrangeiro em conta nova (erro 130497). Para
   falar com celulares do Brasil, adicione um **número brasileiro** à conta do WhatsApp
   Business (*Configuração da API → Adicionar número*) e anote o **Phone number ID**.
3. **Token permanente** (o temporário vence em 24h e cai ao sair do Facebook): em
   [business.facebook.com/settings](https://business.facebook.com/settings) →
   *Usuários → Usuários do sistema* → crie um (Administrador), atribua o **app** e a
   **conta do WhatsApp** com controle total e **Gere novo token** (validade "Nunca";
   permissões `whatsapp_business_messaging`, `whatsapp_business_management` e
   `business_management`).
4. **`.env`** (depois `docker compose up -d --build --wait`):
   ```bash
   WHATSAPP_PROVEDOR=meta
   META_ACCESS_TOKEN=...            # token do usuário do sistema
   META_PHONE_NUMBER_ID=...
   META_APP_SECRET=...
   META_VERIFY_TOKEN=qualquer-texto-que-voce-inventar
   FOLLOWUP_UNIDADE=minutos          # demo: cadência em minutos
   ```
5. **Webhook** — *WhatsApp → Configuração → Webhook*: URL de callback
   `https://<seu-dominio>/webhooks/whatsapp/meta`, o mesmo `META_VERIFY_TOKEN`, e assine
   o campo **messages**. (Também dá para trocar a URL pela API:
   `POST /{app-id}/subscriptions` com o token do app `app_id|app_secret`.)
6. **Inscreva o app na conta do WhatsApp Business do número** — sem isso só o botão
   "Testar" do painel chega, não as mensagens reais:
   ```bash
   curl -X POST "https://graph.facebook.com/v22.0/<WABA_ID>/subscribed_apps" \
     -H "Authorization: Bearer $META_ACCESS_TOKEN"
   ```
   O `<WABA_ID>` aparece em *Configurações do negócio → Contas → Contas do WhatsApp*
   (ou via `GET /<business-id>/owned_whatsapp_business_accounts`).
7. Do celular, mande "oi" para o número. No Streamlit, o lead aparece como
   `📱 whatsapp:+55...` (o chat é só leitura; a equipe responde pela tela Fila).

#### Twilio

1. No Console (busca "WhatsApp sandbox"), mande do celular o `join <código>` para o número
   de teste da conta e use esse número em `TWILIO_WHATSAPP_FROM`. **Conta trial só envia
   por Content Template** (erro 21654): para texto livre, é preciso o upgrade.
2. `.env`: `WHATSAPP_PROVEDOR=twilio`, `PUBLIC_BASE_URL=https://<seu-tunel>`,
   `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_WHATSAPP_FROM=whatsapp:+<número>`.
3. *Sandbox settings* → "When a message comes in" =
   `https://<seu-tunel>/webhooks/whatsapp/twilio` (POST).

Com o cloudflared (ou ngrok sem domínio fixo), a URL muda a cada execução: atualize o
webhook no painel (e, no Twilio, o `PUBLIC_BASE_URL`). Assinatura inválida ⇒ 403 no log da API. Templates para aprovação:
[docs/whatsapp-templates.md](docs/whatsapp-templates.md) (gerado por
`uv run python -m sdr.cli templates-doc`). Telefones aparecem mascarados nos logs.

### Observabilidade (Langfuse)

Cada turno, follow-up e resumo vira um trace no Langfuse, agrupado pela sessão do lead:
um span por nó do grafo e uma geração por chamada ao LLM (modelo, tokens, custo e
latência). Telefone, e-mail e CPF são mascarados antes do envio
([ADR 013](docs/adr/013-observabilidade-langfuse.md)).

1. Crie uma conta grátis em [cloud.langfuse.com](https://cloud.langfuse.com) (ou
   us.cloud.langfuse.com), um projeto e, em *Settings → API Keys*, um par de chaves.
2. `.env`: `LANGFUSE_PUBLIC_KEY=pk-lf-...`, `LANGFUSE_SECRET_KEY=sk-lf-...` e
   `LANGFUSE_BASE_URL` (a região da conta); depois `docker compose up -d --build --wait`.
3. Converse: cada resposta no chat (e na Fila) tem o link **🔎 trace**; no Langfuse,
   *Sessions* agrupa a conversa do lead (session = id do lead).

Sem as chaves, nada é enviado.

### Dashboard

http://localhost:8501 abre o dashboard (Streamlit multipágina, só via API):
**Visão geral** (KPIs, funil, intenção, score, canal), **Leads** (tabela filtrável +
detalhe com conversa, ficha, score e motivos, timeline, resumo, agendamentos,
follow-ups e link para o trace), **Fila de atendimento** (espera, SLA excedido, assumir,
responder, devolver), **Agenda** (por responsável e por dia) e **Follow-ups**
(pendentes, enviados, reengajamento, opt-outs). O chat com a Lia fica em
*Demonstração → Chat com a Lia*. Período na barra lateral (padrão: 14 dias).

```bash
make seed-demo      # ~40 leads sintéticos nos últimos 14 dias (marcados como demo)
make limpar-demo    # remove todos os dados de demo
```

API: `GET /painel/visao-geral`, `GET /painel/leads`, `GET /painel/followups`
(`?inicio=&fim=` em ISO 8601) — [ADR 014](docs/adr/014-dashboard-consultas.md).

## Desenvolvimento

```bash
uv sync          # instala dependências (inclui dev)
make check       # ruff + mypy + import-linter + pytest
make format      # aplica ruff fix + format
```

Testes de integração (`tests/integration/`) usam o Postgres do compose e são pulados
automaticamente se ele não estiver de pé (`docker compose up -d db`).

Testes com o LLM real ficam fora do `make check` (custam tokens e precisam da API no ar):
`make e2e` (`-m e2e`) e `make llm` (`-m llm`, roda os cenários de `evals/cenarios/`).
