# CLAUDE.md — Agente SDR Conversacional (vertical ativa: imobiliário)

## Fase atual: Dia 2 — Etapa C entregue (aguardando ok da PO)

Ao fim de CADA etapa: parar, listar como verificar o critério de aceite e esperar o ok da PO.
Commits só com ok da PO, um por etapa, na branch `dia-2` (nunca direto na `main`).

### Dia 2 — Etapa C (Exposição e verificação) — ENTREGUE, aguardando aceite da PO
Entregue: `GET /leads/{id}` (caso de uso `ObterLead` no core), painel de qualificação ao
lado do chat (Streamlit, fragment com polling), cenários `pytest -m llm` (`make llm`) lidos
de `evals/cenarios/*.json`, migration 0006 (`lead_eventos.seq`: ordem estável dos eventos
do mesmo instante) e prompt de extração v2 (valores da intenção anterior não vazam para a
nova ficha na troca de intenção — achado do cenário 3). Especificação original abaixo.
- `GET /leads/{id}` (id = `lead_id` público do canal web, como nas demais rotas): intenção,
  ficha, campos_faltantes, score, classificação, score_motivos, proxima_acao e eventos.
  `campos_faltantes` não é persistido: calcular com `campos_faltantes(ficha, prioridade)`
  usando a `IntencaoVertical` da intenção atual (VerticalMontada.intencoes) — no core, sem
  conhecer imóvel (caso de uso genérico, ex.: `ObterLead`).
- Streamlit: painel lateral AO LADO do chat mostrando em tempo real intenção, ficha sendo
  preenchida, score com motivos e próxima ação — consumindo SÓ a API (dentro do mesmo
  `st.fragment(run_every=...)` do polling, ou outro fragment).
- Testes de cenário com LLM real marcados `@pytest.mark.llm` (fora da suíte padrão: incluir
  em `addopts` junto com e2e), roteirizando contra a API no ar (reaproveitar `postar` /
  `aguardar_resposta` de tests/e2e/test_conversa_lia.py):
  1. "Estou procurando apartamento na zona sul" → qualifica compra até
     proxima_acao = agendar_visita
  2. "Quero investir em imóveis para renda" → qualifica investimento até
     proxima_acao = encaminhar_especialista
  3. troca de intenção de aluguel para compra no meio da conversa (IntencaoAlterada,
     ficha de aluguel preservada, regiao/quartos herdados)
  Salvar os roteiros em `evals/cenarios/` (YAML ou JSON: falas do lead + expectativas),
  pois serão reaproveitados no eval do Dia 5; o teste lê os roteiros desses arquivos.
  Asserts robustos a variação do LLM (estado final via GET /leads/{id}, não texto exato).
- Aceite do dia: Exemplos 1 e 2 rodam ponta a ponta no chat com ficha, score e próxima
  ação aparecendo no painel; lint-imports, ruff, mypy e pytest passam; `pytest -m llm`
  passa nos 3 cenários.
- Roteiros que já qualificaram com LLM real na Etapa B (bons pontos de partida):
  compra: "Estou procurando apartamento na zona sul" / "Quero comprar" / "Até uns 800 mil" /
  "2 quartos" / "Quero me mudar nos próximos 2 meses" / "Vou financiar e usar meu FGTS".
  investimento: "Quero investir em imóveis para renda" / "Tenho uns 500 mil pra investir" /
  "Algo em torno de 6% ao ano" / "Penso no longo prazo, mais de 5 anos" /
  "Seria meu primeiro investimento em imóvel".
  Cada fala precisa esperar a resposta (debounce de 5s) antes da próxima.
- NÃO implementar: agendamento, resumo para corretor, follow-up, dashboard, WhatsApp.

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
                           (ItemCatalogo...), qualificacao.py (agregado Qualificacao com as
                           regras de intenção/merge/faltantes/score; IntencaoVertical,
                           SchemaFicha, RegrasQualificacao, Score), eventos.py (EventoLead);
                           depois Agendamento
    application/
      ports/               Protocols: CatalogoPort, EmbeddingPort, VerificadorSaudePort, LLMPort
                           (texto + saída estruturada), AgenteConversacionalPort, Ferramenta,
                           LeadRepository, ConversaRepository, LeadEventoRepository,
                           AgendadorTurnoPort, TravaTurnoPort, CanalMensagemPort (prevê
                           TEMPLATE fora da janela de 24h do WhatsApp); depois AgendaPort, CRMPort
      ferramentas/         tools genéricas do agente (FerramentaBuscarCatalogo → CatalogoPort)
      use_cases/           um caso de uso por arquivo: ReceberMensagem (entrada única de todos
                           os canais: grava PENDENTE e agenda), ProcessarTurno (agrega pendentes,
                           roda o grafo uma vez, entrega via canal), ObterHistorico,
                           ListarLeads, ObterLead (qualificação + campos_faltantes derivados
                           das intenções da vertical + eventos), RecuperarTurnosPendentes,
                           VerificarSaude
      dto/                 MensagemRecebida normalizada e agnóstica de canal
                           (canal, remetente_id, texto, timestamp, metadados)
    adapters/              infraestrutura GENÉRICA, reaproveitada por qualquer vertical
      inbound/http/        app FastAPI (registra routers da vertical), /health, GET /leads,
                           GET /leads/{lead_id} (qualificação; 404 se não existe),
                           POST /conversas/mensagens (202) e GET /conversas/{lead_id}/mensagens
      outbound/persistence/  engine async (SQLAlchemy + psycopg 3), Base ORM compartilhado,
                             leads (fichas JSONB + projeções da qualificação), conversas,
                             mensagens, lead_eventos
      outbound/persistence/  + trava_turno_postgres.py (advisory lock por lead)
      outbound/turnos/       AgendadorDebounce (asyncio in-process; produção: fila/Redis)
      outbound/canais/       CanalWeb (resposta já persistida; front faz polling)
      outbound/embeddings/   fastembed (ONNX) local multilíngue (ADR 002)
      outbound/llm/          LLMOpenAI (Chat Completions + tool calling); provedor via
                             LLM_PROVIDER, modelo via LLM_MODELO (ADR 004)
      outbound/agent/        AgenteQualificador (LangGraph): roteador → extração → scoring →
                             especialista ⇄ ferramentas | descoberta (ADR 005); LLM por nó;
                             prompts genéricos em agent/prompts/ (roteador_v1, extracao_v2);
                             memória vem do banco, não do LangGraph
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
      persona/             Lia: lia.py + prompts/<versao>.md (IMOBILIARIO_VERSAO_PROMPT),
                           prompts/descoberta_v1.md, prompts/especialistas/<intencao>_v1.md
      qualificacao/        domain/regras.py (CAMPOS/ESSENCIAIS por intenção, scoring com
                           score_motivos, critério de qualificado — puro); adapters/fichas.py
                           (Pydantic por intenção), schema_pydantic.py (SchemaFicha sobre
                           Pydantic), intencoes.py (compra/aluguel/investimento) — regras de
                           negócio em docs/qualificacao-imobiliaria.md
  config/                  pydantic-settings GENÉRICO (inclui VERTICAL=imobiliario)
  bootstrap.py             composition root — instancia a infra do core, escolhe a vertical
                           pelo registro VERTICAIS e chama pack.montar(infra)
  main.py                  entrypoint ASGI (uvicorn sdr.main:app)
  cli.py                   python -m sdr.cli seed — carga inicial do catálogo da vertical
web/                       Streamlit: chat + painel de qualificação ao lado (dois
                           st.fragment com polling) + seletor de lead_id — SOMENTE via API HTTP
worker/                    (Dia 3) processo separado para follow-up
migrations/                Alembic — histórico ÚNICO para core + verticais
tests/apoio/               fakes dos ports e fábricas (core e por vertical); api_viva.py
                           (postar/aguardar_resposta/obter_lead contra a API no ar);
                           cenarios.py (carrega evals/cenarios e confere o estado final)
tests/unit/{core,verticals/imobiliario}/          sem banco, sem LLM
tests/integration/{core,verticals/imobiliario}/   @pytest.mark.integration; banco sdr_test
tests/e2e/                 @pytest.mark.e2e — LLM real contra a API no ar (opt-in: make e2e)
tests/llm/                 @pytest.mark.llm — cenários de qualificação com LLM real (make llm)
evals/cenarios/            roteiros JSON (falas do lead + estado final esperado) — reusados
                           no eval do Dia 5
docs/arquitetura.md  diagramas Mermaid · docs/adr/  decisões de arquitetura
```

### Regras (inegociáveis)

- **Core × vertical**
  - Nada de imóvel/corretor/metrô em `core/` (`tests/unit/core/test_core_agnostico.py`).
  - O core enxerga a vertical só por `VerticalPack`/`VerticalMontada` e por ports genéricos
    (ex.: `CatalogoPort`, com filtros como dicionário no vocabulário da vertical).
  - A vertical ativa é escolhida no bootstrap via `VERTICAL=imobiliario`.
  - `VerticalPack` cresce só quando um campo é USADO (YAGNI). Hoje: catálogo, rotas, carga
    inicial, persona, ferramentas, intenções (schema, prioridade de campos, prompt do
    especialista, próxima ação), regras de qualificação e prompt de descoberta. Depois:
    cadência de follow-up (Dia 3). Sem segunda vertical e sem motor de configuração genérico.
  - **Ficha de qualificação:** uma por intenção, em **JSONB** (`{"compra": {...}}`), validada
    campo a campo pelo schema da vertical (Protocol `SchemaFicha`; Pydantic só na vertical).
    Intenção "indefinida" é do core; a vertical nunca a declara.
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
- **Turnos assíncronos (ADR 006)**
  - Chat web e WhatsApp entram pelo **mesmo caso de uso** (`ReceberMensagem`): grava como
    PENDENTE, agenda e retorna (web: HTTP 202). A resposta NUNCA volta no recebimento.
  - `ProcessarTurno` agrega as pendentes do lead e roda o grafo uma vez; mensagem que chega
    antes do envio ⇒ descarta e reprocessa; depois do envio ⇒ próximo turno.
  - Debounce por lead (DEBOUNCE_SEGUNDOS, teto DEBOUNCE_MAX_SEGUNDOS); advisory lock do
    Postgres por lead; resposta sai pelo `CanalMensagemPort` do canal.
  - POC: agendador in-process ⇒ API com 1 réplica; produção troca o adapter por fila.
- **Agente (ADR 004)**
  - LLM só via `LLMPort` (nunca SDK/LangChain no núcleo); LangGraph só orquestra.
  - Memória = histórico no banco (sem checkpointer); pendentes não entram no histórico.
  - Tools do agente chamam ports, nunca o banco.
  - Nada fora da base: códigos citados são conferidos no catálogo; código inexistente ⇒ uma
    correção; persistindo ⇒ fallback da persona.
  - Prompts versionados em arquivo; toda resposta grava prompt_versao, modelo e tokens.
- **Qualificação (ADR 005)**
  - Regras universais (troca de intenção, merge, faltantes, eventos) no agregado
    `Qualificacao` do domínio; nós do grafo só orquestram. Scoring/critério são da vertical.
  - Merge: nulo nunca apaga; campo preenchido só muda com correção explícita e só some com
    remoção explícita. Especialista faz no máximo UMA pergunta (próximo campo por prioridade).
  - `IntencaoVertical.campos_para_sugerir`: preenchidos ⇒ o especialista busca no catálogo.
    Qualificado ⇒ não pergunta mais dados; a única pergunta conduz à `proxima_acao`.
  - Toda mudança relevante vira evento em `lead_eventos`.
- **Gerais**
  - Toda config e chave via `.env` (commitar `.env.example`; nunca segredos).
  - Código e nomes de domínio em **português**; termos técnicos consagrados em inglês.
  - Toda decisão arquitetural relevante vira ADR em `docs/adr/`.

### Contratos do import-linter

1. Camadas globais: `(main | cli) > bootstrap > (verticals | config) > core`.
2. `sdr.core` não importa `sdr.verticals`.
3. Hexágono do core: `vertical > adapters > application > domain` (container `sdr.core`).
4. Vertical imobiliária: `pack > (config | catalogo | persona | qualificacao)`; fatias
   hexagonais: `catalogo` (`adapters > application > domain`) e `qualificacao`
   (`adapters > domain`).
5. Núcleos puros (domain/application do core, catalogo e qualificacao.domain) não importam fastapi, starlette,
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
- Debounce: `DEBOUNCE_SEGUNDOS` (5) e `DEBOUNCE_MAX_SEGUNDOS` (20).
- LLM por nó: `LLM_MODEL_ROUTER`, `LLM_MODEL_EXTRACTION`, `LLM_MODEL_AGENT` (vazio = `LLM_MODELO`);
  `ROUTER_CONFIANCA_MIN` (0.6) para trocar de intenção.
- `make e2e` — aceite da Etapa C (Dia 1) com LLM real contra a API no ar (custa tokens; precisa
  de OPENAI_API_KEY no .env). Fora do `make check` (`addopts = -m 'not e2e and not llm'`).
- `make llm` (= `pytest -m llm -v -s`) — cenários de qualificação com LLM real lidos de
  `evals/cenarios/*.json` (compra → agendar_visita, investimento → encaminhar_especialista,
  troca aluguel → compra); asserts no estado via `GET /leads/{id}` e no painel (AppTest).
  Cada fala espera a resposta (debounce). ~2–3 min. Novo cenário = novo JSON, sem código.
- `GET /leads/{lead_id}` — estado de qualificação (intenção, ficha, fichas, campos_faltantes,
  score, classificação, score_motivos, próxima ação, eventos).
- Chat: http://localhost:8501 (Streamlit) ou `POST /conversas/mensagens {lead_id, texto}` (202)
  + polling em `GET /conversas/{lead_id}/mensagens` (`processando` = Lia digitando).
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
- **Dia 2** — Qualificação + multiagentes
  - Etapa 0 — Processamento assíncrono com debounce (ReceberMensagem → agendador →
    ProcessarTurno; advisory lock; HTTP 202 + polling) — ADR 006
  - Etapa A — Grafo genérico no core: roteador de intenção (com troca e "indefinida" →
    descoberta) → extração estruturada com merge incremental da ficha (JSONB) → scoring →
    especialista; VerticalPack com intenções/schemas/prioridades/scoring/critério/prompts;
    eventos em `lead_eventos`; modelo LLM por nó (LLM_MODEL_ROUTER/AGENT/EXTRACTION)
  - Etapa B — Vertical imobiliária: intenções compra/aluguel/investimento, schemas Pydantic,
    scoring por regras com score_motivos, critério de qualificado e proxima_acao,
    prompts dos especialistas (persona Lia)
  - Etapa C — GET /leads/{id}, painel de qualificação no Streamlit, cenários com LLM real
    (`pytest -m llm`) salvos em evals/cenarios/ — entregue
  - Aceite do dia: Exemplos 1 e 2 do enunciado ponta a ponta no chat com ficha, score e
    próxima ação no painel; lint-imports, ruff, mypy e pytest passam; `pytest -m llm` passa
- **Dia 3** — Agenda mock de corretores + resumo para corretor + worker de follow-up
- **Dia 4** — Dashboard + WhatsApp (Twilio Sandbox) + observabilidade Langfuse
- **Dia 5** — Deploy cloud + guardrails/mascaramento de PII + eval + README e docs finais

## Fora de escopo agora

- Segunda vertical e motor de configuração genérico de verticais (YAGNI — ADR 003).
- Agendamento de visita (no Dia 2 só se marca `proxima_acao`), resumo para corretor e
  follow-up (Dia 3).
- Agenda, resumo para corretor, worker de follow-up (Dia 3).
- Dashboard, WhatsApp/Twilio, Langfuse (Dia 4).
- Deploy, guardrails/PII, eval (Dia 5).
- Autenticação da API.
- Full-text/BM25 na busca, reranker e interpretador via LLM (avaliar no eval do Dia 5).
- Endpoint de cadastro/edição de imóveis (carga só via `python -m sdr.cli seed`).
