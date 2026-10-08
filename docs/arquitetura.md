# Arquitetura — Agente SDR Conversacional (vertical imobiliária)

Este documento descreve como o sistema é organizado. Ele se apoia em três decisões:

- **hexagonal (Ports & Adapters):** [ADR 001](adr/001-arquitetura-hexagonal.md);
- **busca híbrida com embeddings locais:** [ADR 002](adr/002-busca-hibrida-embeddings-locais.md);
- **core de SDR genérico separado das verticais de negócio:**
  [ADR 003](adr/003-core-multi-segmento.md);
- **agente com LLM por port, LangGraph como orquestrador e memória no banco:**
  [ADR 004](adr/004-agente-llm-memoria.md);
- **grafo genérico de qualificação (roteador, extração, scoring, especialistas):**
  [ADR 005](adr/005-grafo-qualificacao-multiagente.md);
- **turnos assíncronos com agregação de mensagens (debounce):**
  [ADR 006](adr/006-processamento-assincrono-debounce.md);
- **agendamento (AgendaPort, mock em Postgres, nó de agenda no grafo):**
  [ADR 007](adr/007-agendamento-agenda-mock.md);
- **resumo para o responsável fora do turno, ancorado, e CRMPort:**
  [ADR 008](adr/008-resumo-handoff-crm.md).

## 1. Contexto

Quem usa o sistema e com quais sistemas externos ele conversa. Elementos tracejados chegam
nos dias seguintes do cronograma.

```mermaid
flowchart LR
    lead([Lead<br/>interessado em imóvel])
    corretor([Corretor])
    gestor([Gestor comercial])

    subgraph sdr[Agente SDR — vertical imobiliária]
        sistema[Lia — consultora virtual<br/>qualifica, busca imóveis, agenda]
    end

    openai[(OpenAI API<br/>LLM)]
    whatsapp[(WhatsApp<br/>Twilio)]
    langfuse[(Langfuse<br/>observabilidade)]

    lead -- chat web --> sistema
    lead -. WhatsApp .-> whatsapp -. webhook .-> sistema
    sistema -- gera respostas --> openai
    sistema -. resumo do lead + visita .-> corretor
    gestor -. dashboard .-> sistema
    sistema -. traces .-> langfuse
```

## 2. Core genérico × vertical

O **core** sabe fazer SDR conversacional para qualquer segmento: lead, conversa, mensagem,
qualificação, score, agendamento, follow-up e catálogo genérico. A **vertical** ensina o
negócio: o que é o catálogo (imóvel), quais intenções existem, como qualificar e pontuar, e
qual a persona. As duas se encontram no contrato `VerticalPack`.

```mermaid
flowchart TB
    boot[[bootstrap.py<br/>VERTICAL=imobiliario]]

    subgraph core[sdr.core — NÃO conhece imóvel]
        contrato{{vertical.py<br/>VerticalPack · InfraCompartilhada · VerticalMontada}}
        cport{{CatalogoPort<br/>ConsultaCatalogo → ResultadoCatalogo · ItemCatalogo}}
        infra[adapters genéricos<br/>HTTP app · engine/Base ORM · embeddings · LLM OpenAI · agente LangGraph]
    end

    subgraph vert[sdr.verticals.imobiliario]
        pack[[pack.py — PackImobiliario<br/>composition root da vertical]]
        cat[catalogo/<br/>Imovel · BuscarImoveis · índice pgvector<br/>CatalogoImobiliario · rota /imoveis/busca]
        persona[persona/ — Lia + prompts versionados]
        qualif[qualificacao/<br/>intenções · fichas Pydantic · regras de scoring]
    end

    boot -- "1. cria a infra" --> infra
    boot -- "2. pack.montar(InfraCompartilhada)" --> pack
    pack -. implementa .-> contrato
    pack --> cat
    cat -. "CatalogoImobiliario implementa" .-> cport
    pack --> persona
    pack --> qualif
    pack -- "3. VerticalMontada: catalogo, persona, ferramentas, routers, carga inicial" --> boot

    classDef futuro stroke-dasharray: 5 5
```

**Regra entre as árvores:** `verticals → core`, nunca o contrário. O import-linter garante a
regra para imports, e `tests/unit/core/test_core_agnostico.py` garante também para o
vocabulário (nenhum "imóvel", "corretor" ou "metrô" em `core/`).

## 3. Componentes (hexágonos)

Cada árvore é um hexágono: o core inteiro, e cada fatia da vertical (`catalogo/` hoje;
`persona/` e `qualificacao/` depois).

```mermaid
flowchart TB
    subgraph clientes[Clientes]
        web[web/ Streamlit]
        wpp[WhatsApp webhook]:::futuro
    end

    subgraph core[sdr.core]
        subgraph cin[adapters/inbound]
            http[http — app FastAPI<br/>/health · /conversas · /leads · /leads/&#123;id&#125;]
        end
        subgraph capp[application — sem frameworks]
            cuc[use_cases<br/>ReceberMensagem · ProcessarTurno · ObterHistorico<br/>ListarLeads · ObterLead · RecuperarTurnosPendentes · VerificarSaude<br/>ConduzirAgendamento · GerarResumoHandoff · ObterResumo · ListarAgendamentos]
            cports{{ports<br/>CatalogoPort · EmbeddingPort · LLMPort · Ferramenta<br/>LeadRepository · ConversaRepository · LeadEventoRepository<br/>AgenteConversacionalPort · AgendadorTurnoPort · TravaTurnoPort · CanalMensagemPort<br/>AgendaPort · RelogioPort · CRMPort · RedatorResumoPort · ResumoRepository · PublicadorEventosPort}}
            cferr[ferramentas<br/>FerramentaBuscarCatalogo]
        end
        cdom[domain — sem frameworks<br/>Lead · Conversa · Mensagem · Persona · ItemCatalogo<br/>Qualificacao · Score · EventoLead<br/>Responsavel · Slot · Agendamento · NegociacaoAgenda]
        subgraph cout[adapters/outbound]
            persist[persistence<br/>engine · Base ORM · leads/conversas/mensagens/lead_eventos<br/>AgendaPostgres — mock: responsaveis/slots_agenda/agendamentos<br/>resumos_handoff · CRMPostgresMock — crm_registros + log JSON]
            emb[embeddings<br/>fastembed]
            llm[llm — OpenAI]
            agent[agent — LangGraph<br/>AgenteQualificador]
        end
    end

    subgraph vcat[sdr.verticals.imobiliario.catalogo]
        vdom[domain<br/>Imovel · CriteriosBusca]
        vapp[application<br/>BuscarImoveis · CadastrarImoveis<br/>ImovelRepository · IndiceImoveisPort · InterpretadorConsultaPort]
        vad[adapters<br/>repositório SQL · índice pgvector · interpretador regras<br/>CatalogoImobiliario · rota /imoveis/busca · carga JSON]
    end

    pg[(Postgres + pgvector)]
    openaiapi[(OpenAI API)]

    web -- HTTP --> http
    wpp -.-> http
    http --> cuc --> cports
    cuc --> cdom
    persist -. implementa .-> cports
    emb -. implementa .-> cports
    llm -. implementa .-> cports
    agent -. implementa .-> cports
    agent -- "executa tools" --> cferr
    cferr -- CatalogoPort --> cports

    vad --> vapp --> vdom
    vad -. "CatalogoImobiliario implementa" .-> cports
    vapp -- usa EmbeddingPort --> cports
    vad -- "Base ORM" --> persist
    http -- "inclui routers da vertical" --> vad

    persist --> pg
    vad --> pg
    llm --> openaiapi

    classDef futuro stroke-dasharray: 5 5
```

`*` = chega nos próximos dias do cronograma.

### Regra de dependência

```mermaid
flowchart RL
    subgraph core[sdr.core]
        cadp[adapters] --> capp[application] --> cdom[domain]
        cvert[vertical.py] --> cadp
    end
    subgraph vert[sdr.verticals.imobiliario]
        pack[pack.py] --> vadp[catalogo.adapters] --> vapp[catalogo.application] --> vdom[catalogo.domain]
        pack --> vcfg[config.py]
    end
    vert --> core
    bootstrap --> vert
    bootstrap --> core
    bootstrap --> config[sdr.config]
    main_cli[main · cli] --> bootstrap
```

As setas apontam **para dentro** e **para o core**. Os contratos do import-linter
(`.importlinter`) são:

| Contrato | O que impede |
|---|---|
| Camadas globais | `core` importar `verticals`/`bootstrap`; `verticals` importar `config` ou `bootstrap` |
| Core não importa verticals | qualquer import de `sdr.verticals` dentro de `sdr.core` |
| Hexágono do core | `domain` importar `application`, `application` importar `adapters` etc. |
| Vertical imobiliária | fatias importarem `pack`; `catalogo` importar `config` da vertical |
| Hexágono das fatias | o mesmo do core, dentro de `verticals/imobiliario/catalogo` |
| Núcleos puros | domain/application (core e fatias) importarem FastAPI, Pydantic, SQLAlchemy, pgvector… |
| Adapters independentes | `core.adapters.inbound` ⟂ `core.adapters.outbound` |
| Web isolado | Streamlit importar o backend ou o banco |

## 4. Montagem na inicialização

```mermaid
sequenceDiagram
    participant M as main / cli
    participant B as bootstrap
    participant P as PackImobiliario
    participant A as app FastAPI (core)

    M->>B: criar_aplicacao() / montar_container()
    B->>B: settings.vertical → VERTICAIS["imobiliario"]
    B->>B: engine, sessões, EmbeddingFastembed, VerificadorSaude
    B->>P: montar(InfraCompartilhada(sessoes, embedding))
    P->>P: valida dimensão do embedding × coluna vector(384)
    P->>P: instancia repositório, índice pgvector, interpretador, casos de uso
    P-->>B: VerticalMontada(catalogo, persona, ferramentas, intencoes, regras, prompt_descoberta, routers, carga)
    B->>B: LLMOpenAI por nó (LLM_MODEL_ROUTER/EXTRACTION/AGENT)
    B->>B: AgenteQualificador(llms, persona, ConfigQualificacao(intencoes, regras, descoberta))
    B->>B: ProcessarTurno(..., trava Postgres, canais={web: CanalWeb})
    B->>B: AgendadorDebounce(DEBOUNCE_SEGUNDOS, DEBOUNCE_MAX_SEGUNDOS) → executa ProcessarTurno
    B->>B: ReceberMensagem(leads, conversas, eventos, agendador)
    B->>B: ObterLead(leads, eventos, vertical.intencoes)
    B->>A: criar_app(Dependencias, routers=vertical.routers)
```

## 5. Fluxo de uma requisição (`GET /health`)

```mermaid
sequenceDiagram
    participant C as Cliente
    participant R as router health (core inbound)
    participant U as VerificarSaude (core use case)
    participant P as VerificadorSaudePostgres (core outbound)
    participant DB as Postgres

    C->>R: GET /health
    R->>U: executar()
    U->>P: verificar() via VerificadorSaudePort
    P->>DB: SELECT 1 + pg_extension 'vector'
    DB-->>P: ok
    P-->>U: ResultadoVerificacao
    U-->>R: StatusSaude
    R-->>C: 200 {"status":"ok"} ou 503 {"status":"degradado"}
```

Os próximos fluxos seguem o mesmo desenho: o router só traduz HTTP ↔ caso de uso. A mensagem
de um lead, venha do chat web ou do WhatsApp, vira um `MensagemRecebida` e entra no mesmo
`ReceberMensagem`, do core.

## 6. Grafo de qualificação e agendamento (um turno)

```mermaid
flowchart TD
    START([mensagem + estado do lead]) --> R["roteador<br/>LLM_MODEL_ROUTER · saída estruturada"]
    R --> D{"Qualificacao.aplicar_intencao<br/>(domínio do core)"}
    D -- "sem intenção" --> DESC["descoberta<br/>prompt de descoberta da vertical"]
    D -- "intenção X (nova, mantida ou trocada)" --> EX["extração<br/>LLM_MODEL_EXTRACTION · schema da intenção X"]
    EX --> M["SchemaFicha.validar (vertical)<br/>+ Qualificacao.aplicar_extracao (merge)"]
    M --> SC["RegrasQualificacao.pontuar / qualificado (vertical)<br/>+ Qualificacao.aplicar_score · campos_faltantes"]
    SC -- "não qualificado" --> ESP["especialista X<br/>LLM_MODEL_AGENT · persona + prompt X<br/>+ bloco de estado (próximo campo, score, próxima ação)"]
    SC -- "qualificado e X tem TipoAgendamento<br/>(ou negociação em curso)" --> AG["agenda<br/>LLM interpreta a fala (ação + preferência)<br/>→ ConduzirAgendamento: RegraAtribuicao (vertical)<br/>+ AgendaPort.listar_disponibilidade + decidir (domínio)<br/>+ reservar/remarcar/cancelar só após o 'sim'"]
    AG -- "lead não quer agendar agora" --> ESP
    AG --> RA["responder_agenda<br/>persona + prompt de agenda<br/>+ bloco com os horários REAIS"]
    ESP <-- "tool calls" --> T["ferramentas<br/>buscar_imoveis → CatalogoPort"]
    DESC <-- "tool calls" --> T
    RA <-- "tool calls" --> T
    ESP --> FIM([texto + Qualificacao + negociação de agenda + eventos])
    DESC --> FIM
    RA --> FIM
```

O caso de uso grava a mensagem da Lia, o `Lead` e os eventos em `lead_eventos`. No `Lead`
entram as colunas, as fichas JSONB e a negociação de agenda (`leads.agenda`). O grafo só
escreve na agenda, pelo `AgendaPort`, e somente depois da confirmação explícita do lead
([ADR 007](adr/007-agendamento-agenda-mock.md)).

### Negociação de horário (domínio `decidir`)

```mermaid
stateDiagram-v2
    [*] --> SemProposta
    SemProposta --> Ofertado: oferece 2–3 horários reais
    Ofertado --> AguardandoConfirmacao: lead indica horário ("quinta à tarde", "a 2ª")
    SemProposta --> AguardandoConfirmacao: lead já indica horário
    AguardandoConfirmacao --> AguardandoConfirmacao: falta modalidade / assunto paralelo
    AguardandoConfirmacao --> Ofertado: lead recusa · slot tomado (alternativas)
    AguardandoConfirmacao --> Agendado: "sim" ⇒ AgendaPort.reservar (idempotente)
    Agendado --> AguardandoConfirmacao: remarcar / cancelar (sempre confirma)
    Agendado --> SemProposta: cancelado
    SemProposta --> NaoInsistir: lead não quer agendar agora
```

### Resumo para o responsável (fora do turno, [ADR 008](adr/008-resumo-handoff-crm.md))

```mermaid
sequenceDiagram
    participant PT as ProcessarTurno
    participant C as CanalMensagemPort
    participant P as PublicadorEventosPort<br/>(asyncio; produção: outbox+fila)
    participant G as GerarResumoHandoff
    participant R as RedatorResumoPort (LLM)
    participant DB as resumos_handoff
    participant CRM as CRMPort (mock → HubSpot)

    PT->>C: envia a resposta da Lia
    PT->>P: publicar(eventos do turno)
    Note over PT: turno termina aqui
    P-->>G: LeadQualificado / AgendamentoCriado / atualização
    G->>G: fatos do lead + impressão digital (igual à última? para)
    G->>R: redigir(fatos, TemplateResumo da vertical)
    R-->>G: rascunho
    G->>G: ancorar (dados do estado; só o que tem lastro; "não informado")
    G->>DB: nova versão
    G->>CRM: cria/atualiza lead + anexa resumo
    G->>G: evento ResumoHandoffGerado
```

## 7. Mensagens do lead → turno → resposta da Lia (assíncrono, [ADR 006](adr/006-processamento-assincrono-debounce.md))

```mermaid
sequenceDiagram
    participant W as Streamlit (canal web)
    participant R as router conversas (core)
    participant RM as ReceberMensagem
    participant AG as AgendadorDebounce
    participant PT as ProcessarTurno
    participant TR as Trava (advisory lock)
    participant DB as Repositórios
    participant G as AgenteQualificador
    participant C as CanalWeb

    W->>R: POST "procuro apê"
    R->>RM: MensagemRecebida
    RM->>DB: grava mensagem PENDENTE (cria Lead/Conversa se preciso)
    RM->>AG: agendar(lead) — abre janela de 5s
    R-->>W: 202
    W->>R: POST "zona sul" (t+1s) / "até 800 mil" (t+3s)
    RM->>AG: agendar(lead) — reinicia a janela (teto 20s desde a 1ª)
    loop polling a cada 1,5s
        W->>R: GET /conversas/{lead_id}/mensagens
        R-->>W: mensagens + processando=true → "Lia está digitando…"
    end
    AG->>PT: executar(lead) após 5s de silêncio
    PT->>TR: pg_try_advisory_lock(lead)
    PT->>DB: pendentes = [procuro apê, zona sul, até 800 mil]
    PT->>G: responder(texto agregado, histórico sem pendentes)
    G-->>PT: resposta + Qualificacao + eventos
    PT->>DB: chegaram novas pendentes? (sim → descarta e reprocessa)
    PT->>DB: grava resposta ENVIADA, lote PROCESSADO, Lead e lead_eventos
    PT->>C: enviar(lead, resposta)
    PT->>TR: unlock
    W->>R: GET … → processando=false, resposta visível
```

A guarda contra itens inventados (códigos citados conferidos no catálogo, uma correção e
depois o fallback) roda dentro do `ProcessarTurno`, antes da checagem de novas mensagens.

## 8. Estado de qualificação e painel (`GET /leads/{lead_id}`)

```mermaid
sequenceDiagram
    participant W as Streamlit — fragment "painel" (polling 1,5s)
    participant R as router leads (core)
    participant U as ObterLead (core use case)
    participant L as LeadRepository
    participant E as LeadEventoRepository

    W->>R: GET /leads/{lead_id}
    R->>U: executar(Canal.WEB, lead_id)
    U->>L: obter_por_remetente → Lead + Qualificacao (colunas + fichas JSONB)
    U->>U: campos_faltantes(ficha, IntencaoVertical.prioridade_campos)<br/>(intenções da vertical injetadas pelo bootstrap; não persistido)
    U->>E: listar(lead.id) — ordem (ocorrido_em, seq)
    U-->>R: EstadoLead | None
    R-->>W: 200 {intencao, ficha, fichas, campos_faltantes, score, classificacao,<br/>score_motivos, proxima_acao, qualificado_em, eventos} · 404
```

O core não interpreta a ficha: devolve os nomes de campo da vertical. O vocabulário de
exibição (rótulos, emojis, formato em reais) fica no front da vertical (`web/app.py`).
Chat e painel são dois `st.fragment(run_every=1.5s)` em colunas lado a lado; só eles se
redesenham, e o campo de mensagem continua livre.

**Cenários com LLM real** (`make llm`): `tests/llm/test_cenarios.py` lê os roteiros de
`evals/cenarios/*.json`, manda cada fala e espera a resposta, como no chat. Depois confere
o estado final via `GET /leads/{id}` e o painel via `streamlit.testing` (AppTest), sem
olhar o texto da Lia.

## 9. Busca híbrida de imóveis (`POST /imoveis/busca`, rota da vertical)

```mermaid
sequenceDiagram
    participant C as Cliente
    participant R as rota /imoveis/busca (vertical)
    participant U as BuscarImoveis (vertical)
    participant I as InterpretadorRegras (vertical)
    participant E as EmbeddingFastembed (core)
    participant X as IndiceImoveisPgvector (vertical)
    participant DB as Postgres + pgvector

    C->>R: {"texto": "apê 2 quartos zona sul até 800 mil perto do metrô", "filtros": {...}}
    R->>U: ConsultaImoveis(texto, criterios explícitos, limite)
    U->>I: interpretar(texto) via InterpretadorConsultaPort
    I-->>U: CriteriosBusca inferidos (apartamento, sul, ≤800k, ≥2q, metrô ≤1000m)
    Note over U: inferidos.sobrescrever_com(explícitos)
    U->>E: gerar_consulta(texto) via EmbeddingPort
    E-->>U: vetor[384]
    U->>X: buscar(criterios, vetor, limite) via IndiceImoveisPort
    X->>DB: SELECT … WHERE filtros ORDER BY embedding <=> vetor LIMIT k
    DB-->>X: linhas + distância
    X-->>U: ImovelEncontrado[] (imóvel + similaridade)
    U-->>R: ResultadoBusca(criterios_aplicados, imoveis)
    R-->>C: 200 {criterios_aplicados, total, resultados}
```

**Pelo core (agente).** O agente não conhece `/imoveis/busca`: ele usa o
`CatalogoPort`. O `CatalogoImobiliario` valida os filtros (chaves desconhecidas são
rejeitadas), chama o mesmo `BuscarImoveis` e devolve `ItemCatalogo` com `resumo` e
`atributos`.

**Carga inicial.** O caminho é `python -m sdr.cli seed` → `VerticalMontada.carregar_catalogo_inicial` → `CadastrarImoveis`:

1. `ImovelRepository.salvar_todos` faz o upsert;
2. `EmbeddingPort.gerar_documentos(texto_semantico)` gera os vetores;
3. `IndiceImoveisPort.indexar` grava os vetores no índice.

## 10. Implantação local

```mermaid
flowchart LR
    browser([Navegador]) -- :8501 --> web[web<br/>Streamlit]
    web -- http://api:8000 --> api[api<br/>FastAPI + uvicorn<br/>alembic upgrade no start<br/>VERTICAL=imobiliario]
    api --> db[(db<br/>pgvector/pg16<br/>host :5433)]
    api --> oai[(OpenAI API)]
```
