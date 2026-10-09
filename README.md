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

### WhatsApp (Twilio Sandbox)

A Lia atende também pelo WhatsApp: mesmas regras, mesmo turno assíncrono e mesma Fila.
Fora da janela de 24h, só saem templates aprovados ([ADR 012](docs/adr/012-canal-whatsapp-templates.md)).

1. **Sandbox:** no Console do Twilio, abra *Messaging → Try it out → Send a WhatsApp
   message* e, do seu celular, mande `join <código>` para o número do Sandbox.
2. **Túnel** para a API local (o Twilio precisa de uma URL pública):
   ```bash
   brew install cloudflared && cloudflared tunnel --url http://localhost:8000
   # ou: ngrok http 8000
   ```
3. **`.env`** (depois `docker compose up -d --build --wait`):
   ```bash
   WHATSAPP_PROVEDOR=twilio
   PUBLIC_BASE_URL=https://<seu-tunel>      # a mesma URL configurada no Twilio
   TWILIO_ACCOUNT_SID=AC...
   TWILIO_AUTH_TOKEN=...
   TWILIO_WHATSAPP_FROM=whatsapp:+14155238886
   FOLLOWUP_UNIDADE=minutos                  # demo: cadência em minutos
   ```
4. **Webhook no Sandbox** (*Sandbox settings*): "When a message comes in" =
   `https://<seu-tunel>/webhooks/whatsapp/twilio` (POST). O status de entrega é pedido a
   cada envio (`/webhooks/whatsapp/twilio/status`); não precisa configurar.
5. Converse pelo celular. No Streamlit, o lead aparece como `📱 whatsapp:+55...` (só
   leitura no chat; a equipe responde pela tela Fila, e a resposta chega no WhatsApp).

O túnel muda de URL a cada execução (cloudflared/ngrok grátis): atualize `PUBLIC_BASE_URL`
(e reinicie a API) e o webhook no Sandbox. Assinatura inválida ⇒ 403 no log da API.
Templates para aprovação na Meta: [docs/whatsapp-templates.md](docs/whatsapp-templates.md)
(gerado por `uv run python -m sdr.cli templates-doc`). Telefones aparecem mascarados nos logs.

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
