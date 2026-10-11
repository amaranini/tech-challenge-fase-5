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

**Túnel** para a API local (os dois provedores precisam de uma URL pública):

```bash
brew install cloudflared && cloudflared tunnel --url http://localhost:8000
# a URL https://….trycloudflare.com aparece num quadro no meio do log; deixe o terminal aberto
```

#### Meta (WhatsApp Cloud API) — recomendado para a demo

1. Em [developers.facebook.com](https://developers.facebook.com), *Meus apps → Criar app*,
   caso de uso **WhatsApp** (ou tipo *Empresa* + produto WhatsApp).
2. *WhatsApp → Configuração da API* (*API Setup*): anote o **Phone number ID** do número de
   teste, gere o **token de acesso** (temporário, 24h) e, em *Para*, cadastre e verifique
   o seu celular como destinatário.
3. *Configurações do app → Básico*: copie a **Chave secreta do app** (App Secret).
4. **`.env`** (depois `docker compose up -d --build --wait`):
   ```bash
   WHATSAPP_PROVEDOR=meta
   META_ACCESS_TOKEN=...          # renove quando expirar (24h)
   META_PHONE_NUMBER_ID=...
   META_APP_SECRET=...
   META_VERIFY_TOKEN=qualquer-texto-que-voce-inventar
   FOLLOWUP_UNIDADE=minutos        # demo: cadência em minutos
   ```
5. *WhatsApp → Configuração* (*Configuration*) → **Webhook → Editar**: URL de callback
   `https://<seu-tunel>/webhooks/whatsapp/meta` e o mesmo `META_VERIFY_TOKEN` → *Verificar
   e salvar* (a API precisa estar no ar). Em *Campos do webhook*, assine **messages**.
6. Do celular, mande "oi" para o número de teste. No Streamlit, o lead aparece como
   `📱 whatsapp:+55...` (só leitura no chat; a equipe responde pela tela Fila).

#### Twilio

1. No Console (busca "WhatsApp sandbox"), mande do celular o `join <código>` para o número
   de teste da conta e use esse número em `TWILIO_WHATSAPP_FROM`. **Conta trial só envia
   por Content Template** (erro 21654): para texto livre, é preciso o upgrade.
2. `.env`: `WHATSAPP_PROVEDOR=twilio`, `PUBLIC_BASE_URL=https://<seu-tunel>`,
   `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_WHATSAPP_FROM=whatsapp:+<número>`.
3. *Sandbox settings* → "When a message comes in" =
   `https://<seu-tunel>/webhooks/whatsapp/twilio` (POST).

O túnel grátis muda de URL a cada execução: atualize o webhook no painel (e, no Twilio,
o `PUBLIC_BASE_URL`). Assinatura inválida ⇒ 403 no log da API. Templates para aprovação:
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
