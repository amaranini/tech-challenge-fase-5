# ADR 016 — Deploy na Railway com imagens multi-stage e CI no GitHub Actions

- **Status:** Aceita
- **Data:** 2026-10-12
- **Relacionados:** [ADR 002](002-busca-hibrida-embeddings-locais.md), [ADR 006](006-processamento-assincrono-debounce.md),
  [ADR 011](011-followup-worker.md), [ADR 015](015-seguranca-guardrails.md) · passo a passo em [docs/deploy.md](../deploy.md)

## Contexto

A entrega pede URL pública com api, worker, web e Postgres com pgvector, migrations
como etapa de pré-deploy, healthchecks, seed idempotente e CI a cada push. O webhook do
WhatsApp (Meta Cloud API) precisa de HTTPS estável (fim do túnel local).

## Decisão

1. **Railway** (e não Render): template de Postgres com pgvector e volume, vários
   serviços do mesmo repositório, `preDeployCommand` e healthcheck no config-as-code
   (`deploy/railway/*.json`), rede privada entre serviços e worker sem HTTP. No Render, o
   worker em segundo plano não tem plano gratuito e o Postgres gratuito expira.
2. **Imagens multi-stage**: builder com uv e o download do modelo de embeddings
   (reprodutível, sem rede no runtime — ADR 002); runtime só com o venv (projeto
   instalado não editável), `/models`, migrations; usuário não root. A mesma imagem
   serve api e worker (só o comando muda).
3. **Pré-deploy** da api: `alembic upgrade head && python -m sdr.cli seed` (idempotente);
   o start é só o uvicorn (`--proxy-headers`, `0.0.0.0`: com `::` o uvicorn escuta só em IPv6
   e o healthcheck IPv4 falha — o web fala com a api pela URL pública). O compose local
   continua migrando no start.
4. **1 réplica da api**: o agendador de debounce e o rate limit são in-process (ADR 006,
   ADR 015). Escalar exige trocar esses adapters (fila/Redis) — roadmap.
5. **CI** no GitHub Actions: ruff, mypy, import-linter, `alembic check` e pytest
   (unitários + integração com Postgres pgvector de serviço), sem testes `@llm`/`@e2e`
   e sem segredos. A Railway pode esperar o CI verde antes de publicar.
6. A URL do banco da plataforma (`postgresql://`) é normalizada para o driver async na
   config.

## Consequências

- Deploy sem passos manuais além da configuração inicial dos serviços e variáveis.
- Lotes de embedding menores (16) para o seed caber em ~0,9 GB de memória.
- Custos por uso (não gratuito); a POC cabe no plano Hobby.
- Trocar de plataforma = outro arquivo de config; as imagens e a app não mudam.
