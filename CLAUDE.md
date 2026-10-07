# CLAUDE.md — Agente SDR Conversacional (vertical ativa: imobiliário)

## Fase atual: Dia 1 — Etapa C (agente conversacional com memória)

Ao fim de CADA etapa: parar, listar como verificar o critério de aceite e esperar o ok da PO.

## Visão do projeto

POC de um **Agente SDR com IA generativa** (Tech Challenge FIAP — Fase 5), com a primeira
vertical sendo **imobiliária**: a "Lia, consultora da imobiliária" atende leads por chat web e,
depois, WhatsApp; entende a intenção (compra/aluguel/investimento), busca imóveis reais da base
(RAG híbrido), qualifica o lead, agenda visita com corretor e faz follow-up.

Desenvolvido por uma pessoa em 5 dias, mas **este código evolui para produção**. Objetivo:
zero retrabalho — na evolução, **trocar adapters, nunca reescrever o núcleo** — e
**reaproveitar o core de SDR em outros segmentos** (ADR 003).

## Arquitetura — Core genérico + verticais, ambos hexagonais

Duas fronteiras, ambas garantidas pelo import-linter (`.importlinter`; violação quebra o build):

1. **Core × vertical.** `src/sdr/core/` é o SDR conversacional genérico e **não conhece imóvel**
   (nem corretor, metrô...). `src/sdr/verticals/<segmento>/` implementa a interface
   `VerticalPack` definida no core. **core não importa verticals; verticals só dependem de core.**
2. **Hexágono** (dentro do core e dentro de cada fatia da vertical): `domain ← application ←
   adapters`. Domain e application NUNCA importam FastAPI, Pydantic, SQLAlchemy, LangGraph,
   OpenAI/Anthropic, Twilio, Streamlit, fastembed, pgvector.

```
src/sdr/
  core/                    SDR genérico — NÃO menciona imóvel (há teste que garante)
    domain/                dataclasses puras: conversa.py (Lead, Conversa, Mensagem, Canal,
                           Papel), agente.py (Persona, RespostaAgente), catalogo.py
                           (ItemCatalogo, ConsultaCatalogo, ResultadoCatalogo); depois
                           Agendamento, Qualificacao, Score
    application/
      ports/               Protocols: CatalogoPort, EmbeddingPort, VerificadorSaudePort, LLMPort,
                           AgenteConversacionalPort, Ferramenta, LeadRepository,
                           ConversaRepository; depois AgendaPort, CRMPort, CanalMensagemPort
                           (prevê TEMPLATE fora da janela de 24h do WhatsApp)
      ferramentas/         tools genéricas do agente (FerramentaBuscarCatalogo → CatalogoPort)
      use_cases/           um caso de uso por arquivo: ProcessarMensagemRecebida (entrada única
                           de todos os canais), ObterHistorico, ListarLeads, VerificarSaude
      dto/                 MensagemRecebida normalizada e agnóstica de canal
                           (canal, remetente_id, texto, timestamp, metadados)
    adapters/              infraestrutura GENÉRICA, reaproveitada por qualquer vertical
      inbound/http/        app FastAPI (registra routers da vertical), /health, /conversas, /leads
      outbound/persistence/  engine async (SQLAlchemy + psycopg 3), Base ORM compartilhado,
                             leads/conversas/mensagens (metadados e ficha em JSONB)
      outbound/embeddings/   fastembed (ONNX) local multilíngue (ADR 002)
      outbound/llm/          LLMOpenAI (Chat Completions + tool calling); provedor via
                             LLM_PROVIDER, modelo via LLM_MODELO (ADR 004)
      outbound/agent/        AgenteLangGraph (agente ⇄ ferramentas) implementando
                             AgenteConversacionalPort; memória vem do banco, não do LangGraph
    vertical.py            contrato VerticalPack + InfraCompartilhada + VerticalMontada
  verticals/
    imobiliario/
      pack.py              PackImobiliario — composition root da vertical
      config.py            SettingsImobiliario (env com prefixo IMOBILIARIO_)
      dados/imoveis.json   catálogo fictício (60 imóveis em SP)
      catalogo/            fatia "catálogo", hexagonal:
        domain/            Imovel, CriteriosBusca, ImovelEncontrado
        application/       ports (ImovelRepository, IndiceImoveisPort, InterpretadorConsultaPort)
                           e use_cases (BuscarImoveis, CadastrarImoveis)
        adapters/          ORM/repositório SQL, índice pgvector, interpretador por regras,
                           esquema de filtros (Pydantic), CatalogoImobiliario (→ CatalogoPort),
                           definição da tool buscar_imoveis, rotas /imoveis, carga do JSON
      persona/             Lia: lia.py + prompts/<versao>.md (IMOBILIARIO_VERSAO_PROMPT)
      (Dia 2)   qualificacao/  schema Pydantic da ficha por intenção, scoring, especialistas
  config/                  pydantic-settings GENÉRICO (inclui VERTICAL=imobiliario)
  bootstrap.py             composition root — instancia a infra do core, escolhe a vertical
                           pelo registro VERTICAIS e chama pack.montar(infra)
  main.py                  entrypoint ASGI (uvicorn sdr.main:app)
  cli.py                   python -m sdr.cli seed — carga inicial do catálogo da vertical
web/                       Streamlit (chat + seletor/criação de lead_id) — SOMENTE via API HTTP
worker/                    (Dia 3) processo separado para follow-up
migrations/                Alembic — histórico ÚNICO para core + verticais
tests/apoio/               fakes dos ports e fábricas (core e por vertical)
tests/unit/{core,verticals/imobiliario}/          sem banco, sem LLM
tests/integration/{core,verticals/imobiliario}/   @pytest.mark.integration; banco sdr_test
tests/e2e/                 @pytest.mark.e2e — LLM real contra a API no ar (opt-in: make e2e)
docs/arquitetura.md  diagramas Mermaid · docs/adr/  decisões de arquitetura
```

### Regras (inegociáveis)

- **Core × vertical**
  - Nada de imóvel/corretor/metrô em `core/` (`tests/unit/core/test_core_agnostico.py`).
  - O core enxerga a vertical só por `VerticalPack`/`VerticalMontada` e por ports genéricos
    (ex.: `CatalogoPort`, com filtros como dicionário no vocabulário da vertical).
  - A vertical ativa é escolhida no bootstrap via `VERTICAL=imobiliario`.
  - `VerticalPack` cresce só quando um campo é USADO (YAGNI). Hoje: catálogo, rotas, carga
    inicial, persona e ferramentas. Depois: intenções, validador da ficha, scoring,
    especialistas (Dia 2); cadência de follow-up (Dia 3). Sem segunda vertical e sem motor
    de configuração genérico.
  - **Ficha de qualificação do lead:** persistida como **JSONB** (no core, opaca) e validada
    pelo schema Pydantic da vertical — o core só conhece um Protocol de validação, nunca
    Pydantic. Vale da Etapa C em diante.
  - Tabelas da vertical usam o `Base` do core; migrations num histórico único. Migrations já
    aplicadas NUNCA são reescritas — mudança de schema = migration nova.
- **Hexágono**
  - Domain e DTOs são `dataclass` puros. Pydantic só em adapters (HTTP, esquemas de filtro/
    ficha) e config.
  - `config` e `verticals` não se importam: a vertical tem config própria.
  - `core.adapters.inbound` e `core.adapters.outbound` não se importam.
  - Rotas do core recebem casos de uso via `Dependencias` em `app.state`; rotas da vertical
    são criadas pelo pack já com seus casos de uso (`criar_router(buscar_imoveis)`).
  - Instanciar adapters: só no `bootstrap.py` (infra do core) e no `pack.py` (da vertical).
- **Agente (ADR 004)**
  - Chat web e WhatsApp entram pelo **mesmo caso de uso** (`ProcessarMensagemRecebida`).
  - LLM só via `LLMPort` (nunca SDK/LangChain no núcleo); LangGraph só orquestra.
  - Memória = histórico no banco (sem checkpointer). Mensagem do lead é gravada ANTES do LLM.
  - Tools do agente chamam ports, nunca o banco.
  - Nada fora da base: códigos citados são conferidos no catálogo; código inexistente ⇒ uma
    correção; persistindo ⇒ fallback da persona.
  - Prompts versionados em arquivo; toda resposta grava prompt_versao, modelo e tokens.
- **Gerais**
  - Toda config e chave via `.env` (commitar `.env.example`; nunca segredos).
  - Código e nomes de domínio em **português**; termos técnicos consagrados em inglês.
  - Toda decisão arquitetural relevante vira ADR em `docs/adr/`.

### Contratos do import-linter

1. Camadas globais: `(main | cli) > bootstrap > (verticals | config) > core`.
2. `sdr.core` não importa `sdr.verticals`.
3. Hexágono do core: `vertical > adapters > application > domain` (container `sdr.core`).
4. Vertical imobiliária: `pack > (config | catalogo | persona)`; e cada fatia `adapters > application >
   domain` (container `sdr.verticals.imobiliario.catalogo`).
5. Núcleos puros (domain/application do core e das fatias) não importam fastapi, starlette,
   pydantic, pydantic_settings, sqlalchemy, psycopg, alembic, streamlit, httpx, uvicorn,
   fastembed, onnxruntime, numpy, pgvector, openai, langgraph, langchain_core (adicionar
   twilio etc. quando entrarem como dependência).
6. `core.adapters.inbound` ⟂ `core.adapters.outbound`.
7. `web` não importa `sdr`, sqlalchemy nem psycopg.

Nova fatia na vertical (ex.: `qualificacao/`) ⇒ adicioná-la aos contratos 4 e 5.

## Tooling e comandos

- uv (Python 3.12), ruff (lint + format), mypy strict, pytest (+ pytest-asyncio), import-linter.
- `make check` — ruff + format check + mypy + lint-imports + pytest.
- `docker compose up -d --build --wait` — db (pgvector, host:5433), api (:8000), web (:8501).
  A API roda `alembic upgrade head` no start.
- `make seed` (= `python -m sdr.cli seed`) — carrega o catálogo inicial da vertical ativa +
  embeddings (idempotente). Rodar após subir o compose.
- `make busca q="apê 2 quartos zona sul até 800 mil perto do metrô"` — testa POST /imoveis/busca.
- `make e2e` — aceite da Etapa C com LLM real contra a API no ar (custa tokens; precisa de
  OPENAI_API_KEY no .env). Fora do `make check` (`addopts = -m 'not e2e'`).
- Chat: http://localhost:8501 (Streamlit) ou `POST /conversas/mensagens {lead_id, texto}`.
- Testes de integração usam o banco `sdr_test` (recriado e migrado por sessão); não tocam no seed.
- Nova migration: `uv run alembic revision -m "descricao"`; `uv run alembic check` confirma que
  os modelos ORM batem com as migrations.

## Cronograma

- **Dia 1** — Fundação + imóveis/RAG + agente com memória
  - Etapa A — Fundação (estrutura, tooling, compose, Alembic, /health, docs)
  - Etapa B — Imóveis + busca híbrida (RAG) + POST /imoveis/busca
  - Refactor — core genérico × vertical imobiliária (VerticalPack, CatalogoPort) — ADR 003
  - Etapa C — Agente conversacional (LangGraph + OpenAI) com memória + chat Streamlit
    (Lead/Conversa/Mensagem no core; persona Lia e prompts na vertical)
- **Dia 2** — Roteador de intenção + agentes especialistas (compra/aluguel/investimento) +
  ficha estruturada do lead (JSONB + schema da vertical) + scoring quente/morno/frio
- **Dia 3** — Agenda mock de corretores + resumo para corretor + worker de follow-up
- **Dia 4** — Dashboard + WhatsApp (Twilio Sandbox) + observabilidade Langfuse
- **Dia 5** — Deploy cloud + guardrails/mascaramento de PII + eval + README e docs finais

## Fora de escopo agora

- Segunda vertical e motor de configuração genérico de verticais (YAGNI — ADR 003).
- Qualificação do lead, scoring, roteador de intenção e multiagentes (Dia 2).
- Agenda, resumo para corretor, worker de follow-up (Dia 3).
- Dashboard, WhatsApp/Twilio, Langfuse (Dia 4).
- Deploy, guardrails/PII, eval (Dia 5).
- Autenticação da API.
- Full-text/BM25 na busca, reranker e interpretador via LLM (avaliar no eval do Dia 5).
- Endpoint de cadastro/edição de imóveis (carga só via `python -m sdr.cli seed`).
