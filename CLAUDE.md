# CLAUDE.md — Agente SDR Conversacional (vertical ativa: imobiliário)

## Fase atual: Dia 3 — concluído (tag `dia-3`); próximo: Dia 4

Ao fim de CADA etapa: parar, listar como verificar o critério de aceite e esperar o ok da PO.
Commits só com ok da PO, um por etapa, na branch `dia-3` (nunca direto na `main`); ao fim
do dia, tag `dia-3`. Manter core × vertical e o fluxo assíncrono
(ReceberMensagem → ProcessarTurno).

Dia 3 em 4 etapas (especificação completa no pedido da PO; resumo):
- **A — Agendamento:** `Responsavel` + `AgendaPort` (listar_disponibilidade, reservar,
  remarcar, cancelar) no core; mock em Postgres (responsáveis + slots; seed de 4 — zona sul,
  zona oeste, locação, investimentos — com slots nos próximos 10 dias úteis; produção =
  Google Calendar/Outlook). VerticalPack ganha `regra_atribuicao` e `tipos_agendamento`.
  Nó de agendamento no grafo (proxima_acao agendável): propõe 2–3 horários, entende
  linguagem natural resolvendo para slots reais em America/Sao_Paulo, SEMPRE confirma antes
  de reservar, remarca/cancela, reserva idempotente, slot tomado ⇒ alternativas. Eventos
  AgendamentoCriado/Remarcado/Cancelado. Testes com relógio fake. Aceite: Exemplo 1 agenda
  com "pode ser quinta à tarde?" (confirmação antes); Exemplo 2 marca reunião com o
  especialista em investimentos.
- **B — Resumo para o responsável + CRM:** `GerarResumoHandoff` fora do turno (LeadQualificado,
  AgendamentoCriado, HandoffConfirmado), seções da vertical, ancorado (sem inventar: "não
  informado"), versionado; `CRMPort` mock (crm_registros + log JSON); GET /leads/{id}/resumo,
  GET /agendamentos; painel mostra agendamento e resumo.
- **C — Máquina de estados de atendimento (handoff humano):** domínio puro com transições
  explícitas; gate de estado no início do grafo; confirmação obrigatória; HorarioAtendimento
  (config da operação); autor em cada mensagem (lead | assistente | responsavel); rechecar
  estado antes de enviar; rotas /atendimentos; tela "Fila" no Streamlit.
- **D — Follow-up:** worker/ no compose (mesmos use cases via bootstrap), followups_agendados
  com SKIP LOCKED, cadência da vertical (FOLLOWUP_UNIDADE=dias|minutos), elegibilidade,
  HandoffSLAExcedido, lembrete 24h, opt-out, LeadReengajado, simular-inatividade.
- Aceite do dia: aceites das 4 etapas; lint-imports, ruff, mypy, pytest e `pytest -m llm`
  (inclui Exemplo 3, handoff e retorno para a IA) passam. Commit com tag `dia-3`.
- NÃO implementar: dashboard, WhatsApp, Langfuse, deploy.

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
                           SchemaFicha, RegrasQualificacao, Score), eventos.py (EventoLead),
                           agenda.py (Responsavel, Slot, Agendamento, TipoAgendamento,
                           RegraAtribuicao, PreferenciaHorario, NegociacaoAgenda e `decidir`
                           — negociação pura, confirmação explícita antes de executar),
                           resumo.py (TemplateResumo, FatosResumo, Resumo versionado e a
                           ancoragem: dados do estado, itens citados, evidência literal),
                           atendimento.py (máquina de estados IA × humano com tabela de
                           transições, AcaoAtendimento do turno, HorarioAtendimento),
                           followup.py (cadência, elegibilidade, janela de 24h, SLA em
                           tempo útil)
    application/
      ports/               Protocols: CatalogoPort, EmbeddingPort, VerificadorSaudePort, LLMPort
                           (texto + saída estruturada), AgenteConversacionalPort, Ferramenta,
                           LeadRepository, ConversaRepository, LeadEventoRepository,
                           AgendadorTurnoPort, TravaTurnoPort, CanalMensagemPort (prevê
                           TEMPLATE fora da janela de 24h do WhatsApp), AgendaPort (mock →
                           Google Calendar/Outlook), RelogioPort, CRMPort (mock → HubSpot),
                           RedatorResumoPort, ResumoRepository, PublicadorEventosPort
      ferramentas/         tools genéricas do agente (FerramentaBuscarCatalogo → CatalogoPort)
      use_cases/           um caso de uso por arquivo: ReceberMensagem (entrada única de todos
                           os canais: grava PENDENTE e agenda), ProcessarTurno (agrega pendentes,
                           roda o grafo uma vez, entrega via canal), ObterHistorico,
                           ListarLeads, ObterLead (qualificação + campos_faltantes derivados
                           das intenções da vertical + eventos), RecuperarTurnosPendentes,
                           VerificarSaude, ConduzirAgendamento (slots livres dos aptos →
                           decidir → reservar/remarcar/cancelar só após o "sim"),
                           GerarResumoHandoff (assinante dos eventos, FORA do turno),
                           ObterResumo, ListarAgendamentos, atendimento.py (SolicitarHandoff,
                           ConfirmarHandoff, SolicitarRetornoIA, ConfirmarRetornoIA,
                           AssumirAtendimento, DevolverAtendimento, EnviarMensagemResponsavel,
                           ListarAtendimentos; compare-and-set no estado), followup.py
                           (ProgramarFollowUps — na conversa e assinando eventos;
                           ExecutarFollowUps — chamado pelo worker)
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
      outbound/persistence/  + agenda_postgres.py (AgendaPort mock: responsaveis, slots_agenda,
                             agendamentos; reserva atômica e idempotente; grade no start/seed)
      outbound/persistence/  + resumos_sql.py (resumos_handoff) e crm_postgres.py (CRM mock:
                             crm_registros + log JSON Lines em CRM_MOCK_LOG)
      outbound/eventos/      PublicadorEventosAsyncio (por lead, em ordem; produção: outbox+fila)
      outbound/persistence/  + followups_sql.py (fila followups_agendados, SKIP LOCKED)
      outbound/relogio.py    RelogioSistema (RelogioPort)
      outbound/turnos/       AgendadorDebounce (asyncio in-process; produção: fila/Redis)
      outbound/canais/       CanalWeb (resposta já persistida; front faz polling)
      outbound/embeddings/   fastembed (ONNX) local multilíngue (ADR 002)
      outbound/llm/          LLMOpenAI (Chat Completions + tool calling); provedor via
                             LLM_PROVIDER, modelo via LLM_MODELO (ADR 004)
      outbound/agent/        AgenteQualificador (LangGraph): gate de atendimento → (silêncio |
                             espera | confirmação → responder_atendimento) | roteador
                             (+ atendimento_humano) → extração → scoring →
                             especialista ⇄ ferramentas | descoberta (ADR 005) | agenda →
                             responder_agenda (ADR 007); LLM por nó; prompts genéricos em
                             agent/prompts/ (roteador_v2, extracao_v2, agenda_interpretacao_v1,
                             agendamento_v1, resumo_v1, resumo_checagem_v1, atendimento_v1,
                             atendimento_classificacao_v1); atendimento.py (bloco de fila/horário);
                             agenda.py (interpretação + bloco com
                             horários reais); redator_resumo.py (RedatorResumoPort com LLM);
                             memória vem do banco, não do LangGraph
    vertical.py            contrato VerticalPack + InfraCompartilhada + VerticalMontada
  verticals/
    imobiliario/
      pack.py              PackImobiliario — composition root da vertical
      config.py            SettingsImobiliario (env com prefixo IMOBILIARIO_)
      dados/imoveis.json   catálogo fictício (60 imóveis em SP)
      dados/responsaveis.json  4 responsáveis do mock (zona sul, zona oeste, locação, investimentos)
      agenda/              domain/atribuicao.py (regra: intenção/região → responsável, puro);
                           adapters/agenda_imobiliaria.py (TIPOS_AGENDAMENTO: compra/aluguel →
                           visita_imovel presencial; investimento → reuniao_especialista
                           online|escritorio; leitura dos responsáveis)
      resumo/template.py   TEMPLATE_RESUMO (seções do resumo para o corretor; puro)
      followup/cadencia.py CADENCIAS por situação (D+1, D+3, encerramento D+7) e
                           ConsultaFollowUpImobiliaria (ficha → busca de imóvel novo)
      catalogo/            fatia "catálogo", hexagonal:
        domain/            Imovel, CriteriosBusca, ImovelEncontrado
        application/       ports (ImovelRepository, IndiceImoveisPort, InterpretadorConsultaPort)
                           e use_cases (BuscarImoveis, CadastrarImoveis)
        adapters/          ORM/repositório SQL, índice pgvector, interpretador por regras,
                           esquema de filtros (Pydantic), CatalogoImobiliario (→ CatalogoPort),
                           definição da tool buscar_imoveis, rotas /imoveis, carga do JSON
      persona/             Lia: lia.py + prompts/<versao>.md (IMOBILIARIO_VERSAO_PROMPT=lia_v2),
                           prompts/descoberta_v2.md, prompts/especialistas/<intencao>_v1.md
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
web/                       Streamlit: chat + painel (qualificação, atendimento, agendamento,
                           resumo) + tela "Fila" da equipe (assumir, responder, devolver) —
                           SOMENTE via API HTTP
worker                     `python -m sdr.worker` (src/sdr/worker.py): só o laço de
                           ExecutarFollowUps; serviço `worker` no compose (mesma imagem)
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
    agenda (regra_atribuicao, tipos_agendamento, responsaveis_iniciais), template_resumo e
    follow-up (cadencias_followup — objetivo + template por etapa; lembrete_agendamento;
    consulta_followup). O core não tem texto de mensagem comercial: só as regras gerais. Sem segunda vertical e sem motor de configuração genérico.
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
- **Atendimento humano (ADR 009)**
  - Máquina de estados no domínio; transição fora da tabela = erro. Confirmação obrigatória
    antes de handoff e de voltar para a IA; ambígua ⇒ pergunta UMA vez de novo.
  - Gate no início do grafo; em ATENDIMENTO_HUMANO a IA fica em silêncio (sem LLM).
  - O grafo decide a ação; o turno recheca o estado e aplica com compare-and-set
    (`salvar_atendimento`); `salvar` do lead nunca grava atendimento.
  - Horário de atendimento é da operação (ATENDIMENTO_DIAS/FAIXAS), não da vertical.
  - Mensagens têm autor: lead | assistente | responsavel.
- **Limites do assistente (ADR 010)**
  - Toda resposta leva `limites_v1` (core): o que a IA consegue e o próximo passo REAL para
    o resto (marcar com o responsável / transferir). Nunca "tenho, mas…", nunca "peço para
    alguém mandar". O que a base não tem é declarado na persona da vertical.
  - Roteador: `quer_agendar` (abre a agenda mesmo antes de qualificar) e `fora_do_alcance`
    (tira o "próximo dado" do bloco do especialista nesse turno).
- **Follow-up (ADR 011)**
  - Fila `followups_agendados` com SKIP LOCKED; o worker só chama `ExecutarFollowUps`.
  - Cada resposta do assistente recomeça a cadência; mensagem do lead cancela pendentes
    (e gera LeadReengajado se respondia a um follow-up); opt-out encerra tudo.
  - Elegibilidade e SLA (tempo útil) no domínio; cadência e "item novo" na vertical.
  - Fora da janela de 24h: `requer_template` + `enviar_template`.
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

1. Camadas globais: `(main | cli | worker) > bootstrap > (verticals | config) > core`.
2. `sdr.core` não importa `sdr.verticals`.
3. Hexágono do core: `vertical > adapters > application > domain` (container `sdr.core`).
4. Vertical imobiliária: `pack > followup > agenda > (config | catalogo | persona | qualificacao | resumo)`;
   fatias hexagonais: `catalogo` (`adapters > application > domain`), `qualificacao` e
   `agenda` (`adapters > domain`).
5. Núcleos puros (domain/application do core, catalogo, qualificacao.domain, agenda.domain, resumo e followup) não importam fastapi, starlette,
   pydantic, pydantic_settings, sqlalchemy, psycopg, alembic, streamlit, httpx, uvicorn,
   fastembed, onnxruntime, numpy, pgvector, openai, langgraph, langchain_core (adicionar
   twilio etc. quando entrarem como dependência).
6. `core.adapters.inbound` ⟂ `core.adapters.outbound`.
7. `web` não importa `sdr`, sqlalchemy nem psycopg.

Nova fatia na vertical (ex.: `qualificacao/`) ⇒ adicioná-la aos contratos 4 e 5.

## Tooling e comandos

- uv (Python 3.12), ruff (lint + format), mypy strict, pytest (+ pytest-asyncio), import-linter.
- `make check` — ruff + format check + mypy + lint-imports + pytest.
- `docker compose up -d --build --wait` — db (pgvector, host:5433), api (:8000), web (:8501),
  worker (follow-up; `docker compose logs -f worker`).
  A API roda `alembic upgrade head` no start.
- `make seed` (= `python -m sdr.cli seed`) — carrega o catálogo inicial da vertical ativa +
  embeddings e a agenda mock (responsáveis + grade dos próximos dias úteis; idempotente).
  Rodar após subir o compose. A API também garante a grade no start.
- Agenda: `FUSO_OPERACAO` (America/Sao_Paulo), `AGENDA_ANTECEDENCIA_HORAS` (2),
  `AGENDA_JANELA_DIAS` (14), `AGENDA_SUGESTOES` (3), `AGENDA_MOCK_*` (10 dias úteis, 9h–20h,
  slots de 60 min; ~1 em 4 já ocupado por "outros compromissos").
- `make busca q="apê 2 quartos zona sul até 800 mil perto do metrô"` — testa POST /imoveis/busca.
- Debounce: `DEBOUNCE_SEGUNDOS` (5) e `DEBOUNCE_MAX_SEGUNDOS` (20).
- Follow-up: `FOLLOWUP_UNIDADE=dias|minutos` (demo), `FOLLOWUP_RESPEITAR_HORARIO`,
  `HANDOFF_SLA_MINUTOS` (15), `FOLLOWUP_LEMBRETE_HORAS` (24), `JANELA_CONVERSA_HORAS` (24).
  Demo: `POST /demo/leads/{id}/simular-inatividade` ou botão "⏩ Simular inatividade".
- LLM por nó: `LLM_MODEL_ROUTER`, `LLM_MODEL_EXTRACTION`, `LLM_MODEL_AGENT` (vazio = `LLM_MODELO`);
  `ROUTER_CONFIANCA_MIN` (0.6) para trocar de intenção.
- `make e2e` — aceite da Etapa C (Dia 1) com LLM real contra a API no ar (custa tokens; precisa
  de OPENAI_API_KEY no .env). Fora do `make check` (`addopts = -m 'not e2e and not llm'`).
- `make llm` (= `pytest -m llm -v -s`) — cenários com LLM real lidos de
  `evals/cenarios/*.json` (compra → visita agendada após o "sim", investimento → reunião
  online com a especialista, troca aluguel → compra, remarcar e cancelar); asserts no estado
  via `GET /leads/{id}` (inclusive por fala: `{"texto", "esperado"}`; `contagem_eventos`) e
  no painel (AppTest). Cada fala espera a resposta (debounce). Novo cenário = novo JSON.
- `GET /leads/{lead_id}` — estado de qualificação (intenção, ficha, fichas, campos_faltantes,
  score, classificação, score_motivos, próxima ação, eventos, agendamento ativo).
- `GET /leads/{lead_id}/resumo` (`?versao=N`) — resumo para o responsável (gerado fora do
  turno em LeadQualificado/AgendamentoCriado; 404 enquanto não existe); `GET /agendamentos`
  (`?status=ativo&a_partir_de=...`). CRM mock: tabela `crm_registros` + `var/crm_mock.jsonl`
  (`docker compose exec api tail var/crm_mock.jsonl`). `LLM_MODEL_SUMMARY` para o resumo.
- Atendimento humano: `GET /atendimentos/fila`, `GET /atendimentos?estado=atendimento_humano`,
  `POST /atendimentos/{lead_id}/assumir {responsavel}`, `/devolver`, `/mensagens {texto}`;
  Streamlit → barra lateral "Tela: 🧑‍💼 Fila (equipe)". Horário: `ATENDIMENTO_DIAS=seg-sex`,
  `ATENDIMENTO_FAIXAS=09:00-18:00` (para ver o "fora do horário", rode fora da faixa ou ajuste).
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
- **Dia 3** — Agendamento + resumo para o responsável + atendimento humano + follow-up
  - Etapa A — Agendamento (AgendaPort mock, nó de agendamento, eventos) — ADR 007
  - Etapa B — Resumo de handoff (fora do turno, ancorado) + CRMPort mock + painel — ADR 008
  - Etapa C — Máquina de estados de atendimento (handoff para humano) + tela Fila — ADR 009
  - Etapa D — Worker de follow-up (SKIP LOCKED, cadência da vertical, opt-out, SLA) — ADR 011
- **Dia 4** — Dashboard + WhatsApp (Twilio Sandbox) + observabilidade Langfuse
- **Dia 5** — Deploy cloud + guardrails/mascaramento de PII + eval + README e docs finais

## Fora de escopo agora

- Segunda vertical e motor de configuração genérico de verticais (YAGNI — ADR 003).
- Dashboard, WhatsApp/Twilio, Langfuse (Dia 4).
- Deploy, guardrails/PII, eval (Dia 5).
- Autenticação da API.
- Full-text/BM25 na busca, reranker e interpretador via LLM (avaliar no eval do Dia 5).
- Endpoint de cadastro/edição de imóveis (carga só via `python -m sdr.cli seed`).
