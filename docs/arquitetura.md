# Arquitetura — Agente SDR Imobiliário

Arquitetura hexagonal (Ports & Adapters). Decisão registrada em
[ADR 001](adr/001-arquitetura-hexagonal.md).

## 1. Contexto

Quem usa o sistema e com quais sistemas externos ele conversa. Elementos tracejados chegam
nos dias seguintes do cronograma.

```mermaid
flowchart LR
    lead([Lead<br/>interessado em imóvel])
    corretor([Corretor])
    gestor([Gestor comercial])

    subgraph sdr[Agente SDR Imobiliário]
        sistema[Lia — consultora virtual<br/>qualifica, busca imóveis, agenda]
    end

    anthropic[(Anthropic API<br/>LLM)]
    whatsapp[(WhatsApp<br/>Twilio)]
    langfuse[(Langfuse<br/>observabilidade)]

    lead -- chat web --> sistema
    lead -. WhatsApp .-> whatsapp -. webhook .-> sistema
    sistema -- gera respostas --> anthropic
    sistema -. resumo do lead + visita .-> corretor
    gestor -. dashboard .-> sistema
    sistema -. traces .-> langfuse
```

## 2. Componentes (hexágono)

```mermaid
flowchart TB
    subgraph clientes[Clientes]
        web[web/ Streamlit]
        wpp[WhatsApp webhook]:::futuro
    end

    subgraph inbound[adapters/inbound]
        http[http — routers FastAPI]
    end

    subgraph core[Núcleo — sem frameworks]
        subgraph application[application]
            uc[use_cases<br/>VerificarSaude · BuscarImoveis · ProcessarMensagemRecebida]
            ports{{ports — Protocols<br/>LLMPort · EmbeddingPort · BuscaImoveisPort<br/>Repositories · AgendaPort · CanalMensagemPort<br/>AgenteConversacionalPort · VerificadorSaudePort}}
            dto[dto<br/>MensagemRecebida]
        end
        domain[domain<br/>Lead · Conversa · Mensagem · Imovel · Agendamento<br/>Intencao · PerfilQualificacao · ScoreLead]
    end

    subgraph outbound[adapters/outbound]
        persistence[persistence<br/>SQLAlchemy]
        interp[interpretacao<br/>regras → CriteriosBusca]
        vector[vector<br/>pgvector]
        llm[llm<br/>Anthropic SDK]
        emb[embeddings<br/>modelo local]
        agent[agent<br/>LangGraph]
    end

    pg[(Postgres + pgvector)]
    claude[(Anthropic API)]

    boot[[bootstrap.py<br/>composition root]]
    cfg[config<br/>pydantic-settings]

    web -- HTTP --> http
    wpp -.-> http
    http --> uc
    uc --> ports
    uc --> domain
    uc --> dto
    persistence -. implementa .-> ports
    vector -. implementa .-> ports
    llm -. implementa .-> ports
    emb -. implementa .-> ports
    agent -. implementa .-> ports
    interp -. implementa .-> ports
    agent -- tools chamam --> ports
    persistence --> pg
    vector --> pg
    llm --> claude

    boot --> cfg
    boot -- instancia e injeta --> inbound
    boot -- instancia e injeta --> outbound

    classDef futuro stroke-dasharray: 5 5
```

### Regra de dependência

```mermaid
flowchart RL
    adapters --> application --> domain
    bootstrap --> adapters
    bootstrap --> config
```

As setas apontam **para dentro**. O import-linter (`.importlinter`) garante:

| Contrato | O que impede |
|---|---|
| Camadas | `domain` importar `application`, `application` importar `adapters` etc. |
| Núcleo puro | domain/application importarem FastAPI, Pydantic, SQLAlchemy, psycopg, Streamlit… |
| Adapters independentes | `inbound` importar `outbound` (e vice-versa) — só o bootstrap conecta |
| Web isolado | Streamlit importar o backend ou o banco |

## 3. Fluxo de uma requisição (hoje: `GET /health`)

```mermaid
sequenceDiagram
    participant C as Cliente
    participant R as router health (inbound)
    participant U as VerificarSaude (use case)
    participant P as VerificadorSaudePostgres (outbound)
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

O mesmo desenho vale para os próximos fluxos: o router só traduz HTTP ↔ caso de uso. A
mensagem de um lead, venha do chat web ou do WhatsApp, vira um `MensagemRecebida` e entra no
mesmo `ProcessarMensagemRecebida`.

## 4. Busca híbrida de imóveis (`POST /imoveis/busca`)

```mermaid
sequenceDiagram
    participant C as Cliente
    participant R as router imoveis (inbound)
    participant U as BuscarImoveis (use case)
    participant I as InterpretadorRegras
    participant E as EmbeddingFastembed
    participant B as BuscaImoveisPgvector
    participant DB as Postgres + pgvector

    C->>R: {"texto": "apê 2 quartos zona sul até 800 mil perto do metrô", "filtros": {...}}
    R->>U: ConsultaImoveis(texto, criterios explícitos, limite)
    U->>I: interpretar(texto) via InterpretadorConsultaPort
    I-->>U: CriteriosBusca inferidos (apartamento, sul, ≤800k, ≥2q, metrô ≤1000m)
    Note over U: inferidos.sobrescrever_com(explícitos)
    U->>E: gerar_consulta(texto) via EmbeddingPort
    E-->>U: vetor[384]
    U->>B: buscar(criterios, vetor, limite) via BuscaImoveisPort
    B->>DB: SELECT … WHERE filtros ORDER BY embedding <=> vetor LIMIT k
    DB-->>B: linhas + distância
    B-->>U: ImovelEncontrado[] (imóvel + similaridade)
    U-->>R: ResultadoBusca(criterios_aplicados, imoveis)
    R-->>C: 200 {criterios_aplicados, total, resultados}
```

Carga: `scripts/seed_imoveis.py` → `CadastrarImoveis` → `ImovelRepository.salvar_todos`
(upsert) → `EmbeddingPort.gerar_documentos(texto_semantico)` → `BuscaImoveisPort.indexar`.

## 5. Implantação local

```mermaid
flowchart LR
    browser([Navegador]) -- :8501 --> web[web<br/>Streamlit]
    web -- http://api:8000 --> api[api<br/>FastAPI + uvicorn<br/>alembic upgrade no start]
    api --> db[(db<br/>pgvector/pg16<br/>host :5433)]
```
