# Arquitetura — Agente SDR Conversacional (vertical imobiliária)

Este documento descreve como o sistema é organizado. Ele se apoia em três decisões:

- **hexagonal (Ports & Adapters):** [ADR 001](adr/001-arquitetura-hexagonal.md);
- **busca híbrida com embeddings locais:** [ADR 002](adr/002-busca-hibrida-embeddings-locais.md);
- **core de SDR genérico separado das verticais de negócio:**
  [ADR 003](adr/003-core-multi-segmento.md);
- **agente com LLM por port, LangGraph como orquestrador e memória no banco:**
  [ADR 004](adr/004-agente-llm-memoria.md).

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
        qualif[qualificacao/ — ficha Pydantic, scoring]:::futuro
    end

    boot -- "1. cria a infra" --> infra
    boot -- "2. pack.montar(InfraCompartilhada)" --> pack
    pack -. implementa .-> contrato
    pack --> cat
    cat -. "CatalogoImobiliario implementa" .-> cport
    pack --> persona
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
            http[http — app FastAPI<br/>/health · /conversas · /leads]
        end
        subgraph capp[application — sem frameworks]
            cuc[use_cases<br/>ProcessarMensagemRecebida · ObterHistorico<br/>ListarLeads · VerificarSaude]
            cports{{ports<br/>CatalogoPort · EmbeddingPort · LLMPort · Ferramenta<br/>LeadRepository · ConversaRepository<br/>AgenteConversacionalPort · CanalMensagemPort*}}
            cferr[ferramentas<br/>FerramentaBuscarCatalogo]
        end
        cdom[domain — sem frameworks<br/>Lead · Conversa · Mensagem · Persona · ItemCatalogo<br/>Qualificacao* · Score* · Agendamento*]
        subgraph cout[adapters/outbound]
            persist[persistence<br/>engine · Base ORM · leads/conversas/mensagens]
            emb[embeddings<br/>fastembed]
            llm[llm — OpenAI]
            agent[agent — LangGraph]
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
    P-->>B: VerticalMontada(catalogo, persona Lia, ferramentas=[buscar_imoveis], routers, carga)
    B->>B: LLMOpenAI (LLM_PROVIDER) + AgenteLangGraph(llm, persona, ferramentas)
    B->>B: ProcessarMensagemRecebida(leads, conversas, agente, catalogo, persona)
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
`ProcessarMensagemRecebida`, do core.

## 6. Mensagem do lead → resposta da Lia (`POST /conversas/mensagens`)

```mermaid
sequenceDiagram
    participant W as Streamlit (canal web)
    participant R as router conversas (core)
    participant U as ProcessarMensagemRecebida (core)
    participant DB as Lead/ConversaRepository
    participant G as AgenteLangGraph (core)
    participant L as LLMOpenAI (core)
    participant T as FerramentaBuscarCatalogo → CatalogoPort
    participant V as CatalogoImobiliario (vertical)

    W->>R: {lead_id, texto}
    R->>U: MensagemRecebida(canal=web, remetente_id=lead_id, texto)
    U->>DB: obter/criar Lead e Conversa aberta; últimas N mensagens
    U->>DB: grava mensagem do LEAD (antes do LLM)
    U->>G: responder(lead, histórico, texto)
    G->>L: persona + contexto + histórico (+ lembrete de itens já citados), tools
    L-->>G: tool_call buscar_imoveis(texto, filtros)
    G->>T: executar(argumentos)
    T->>V: buscar(ConsultaCatalogo) → BuscarImoveis (busca híbrida)
    V-->>T: ItemCatalogo[] (IMV-xxx)
    T-->>G: JSON com itens
    G->>L: histórico + resultado da tool
    L-->>G: texto curto citando IMV-001, IMV-005...
    G-->>U: RespostaAgente(texto, itens consultados, tokens)
    U->>V: códigos citados existem? (obter) — senão 1 correção, depois fallback
    U->>DB: grava mensagem da LIA (itens citados, prompt_versao, modelo, tokens)
    U-->>R: ResultadoProcessamento
    R-->>W: {resposta, itens_sugeridos}
```

Reabrir o mesmo `lead_id` reencontra o mesmo Lead (canal + remetente) e a conversa aberta.
O histórico vem do banco, que é a fonte única da memória.

## 7. Busca híbrida de imóveis (`POST /imoveis/busca`, rota da vertical)

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

## 8. Implantação local

```mermaid
flowchart LR
    browser([Navegador]) -- :8501 --> web[web<br/>Streamlit]
    web -- http://api:8000 --> api[api<br/>FastAPI + uvicorn<br/>alembic upgrade no start<br/>VERTICAL=imobiliario]
    api --> db[(db<br/>pgvector/pg16<br/>host :5433)]
    api --> oai[(OpenAI API)]
```
