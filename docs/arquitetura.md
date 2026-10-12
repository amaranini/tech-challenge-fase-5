# Arquitetura — Agente SDR Conversacional (vertical imobiliária)

Como o sistema é organizado e por quê. Para rodar, comece pelo [README](../README.md);
para a parte de IA (modelos, grafo, RAG, guardrails, avaliação), veja [docs/ia.md](ia.md).

**Em uma frase:** um **core de SDR genérico** (lead, conversa, qualificação, agenda,
atendimento humano, follow-up, canais, guardrails) e uma **vertical** que ensina o
negócio (imóveis, intenções compra/aluguel/investimento, persona "Lia"). As duas são
hexagonais: o domínio não conhece framework, banco nem LLM, e trocar infraestrutura é
trocar adapter.

## Sumário

1. [C4 — Contexto](#1-c4--contexto)
2. [C4 — Containers](#2-c4--containers)
3. [C4 — Componentes da API](#3-c4--componentes-da-api)
4. [Core genérico × vertical](#4-core-genérico--vertical)
5. [Um turno de ponta a ponta (do webhook ao envio)](#5-um-turno-de-ponta-a-ponta-do-webhook-ao-envio)
6. [Grafo de agentes (dentro do turno)](#6-grafo-de-agentes-dentro-do-turno)
7. [Máquina de estados do atendimento (IA × humano)](#7-máquina-de-estados-do-atendimento-ia--humano)
8. [Agenda, resumo e follow-up](#8-agenda-resumo-e-follow-up)
9. [Busca híbrida (RAG) e estado de qualificação](#9-busca-híbrida-rag-e-estado-de-qualificação)
10. [Segurança em camadas](#10-segurança-em-camadas)
11. [Implantação: local e nuvem](#11-implantação-local-e-nuvem)
12. [Índice dos ADRs](#12-índice-dos-adrs)

## 1. C4 — Contexto

Quem usa o sistema e com quais sistemas externos ele fala.

```mermaid
flowchart LR
    lead([Lead<br/>quer comprar, alugar ou investir])
    equipe([Equipe comercial<br/>corretores · especialista])
    gestor([Gestor<br/>acompanha a operação])

    subgraph sdr[Agente SDR — vertical imobiliária]
        sistema[Lia, consultora virtual<br/>atende, qualifica, busca imóveis,<br/>agenda, passa para a equipe, faz follow-up]
    end

    meta[(WhatsApp<br/>Meta Cloud API · ou Twilio)]
    openai[(OpenAI API<br/>LLM)]
    langfuse[(Langfuse<br/>observabilidade)]

    lead -- "WhatsApp" --> meta -- "webhook assinado" --> sistema
    sistema -- "respostas, follow-ups, lembretes" --> meta --> lead
    lead -- "chat web (demo)" --> sistema
    equipe -- "dashboard: fila, resumo, agenda" --> sistema
    gestor -- "dashboard: funil, qualidade da IA" --> sistema
    sistema -- "roteador, extração, respostas, resumo" --> openai
    sistema -- "rastros por turno (PII mascarada)" --> langfuse
```

Agenda e CRM são **mocks em Postgres** atrás de ports (`AgendaPort`, `CRMPort`): a troca
por Google Calendar/Outlook e HubSpot é só de adapter ([roadmap](roadmap-producao.md)).

## 2. C4 — Containers

```mermaid
flowchart LR
    subgraph usuarios[ ]
        lead([Lead])
        equipe([Equipe / gestor])
    end
    meta[(Meta Cloud API)]
    openai[(OpenAI)]
    langfuse[(Langfuse)]

    subgraph plataforma[Railway — ou docker compose local]
        api[api<br/>FastAPI + uvicorn · 1 réplica<br/>webhooks · chat · rotas da equipe<br/>turnos com debounce in-process<br/>agente LangGraph]
        worker[worker<br/>python -m sdr.worker<br/>follow-ups · lembretes · SLA da fila]
        web[web<br/>Streamlit multipágina<br/>login · só fala com a API]
        db[(Postgres 16/18 + pgvector<br/>leads · mensagens · lead_eventos<br/>imóveis + embeddings · agenda mock<br/>followups · resumos · CRM mock)]
    end

    lead --> meta -- HTTPS --> api
    equipe -- HTTPS + login --> web -- "HTTPS + X-API-Key" --> api
    api --> db
    worker --> db
    api --> openai
    worker --> openai
    api --> langfuse
    worker --> langfuse
    api --> meta
    worker --> meta
```

| Container | Imagem | Por que separado |
|---|---|---|
| `api` | `docker/api.Dockerfile` (modelo de embeddings embutido) | recebe tudo e roda o turno; **1 réplica** porque o debounce e o rate limit vivem em memória (ADR 006, ADR 015) |
| `worker` | a mesma imagem, outro comando | mensagens ativas não dependem de requisição; seguro para N réplicas (fila com `SKIP LOCKED`) |
| `web` | `docker/web.Dockerfile` (só Streamlit + httpx) | front fino, sem acesso ao banco nem às chaves do LLM/WhatsApp |
| `db` | `pgvector/pgvector` | dados relacionais, JSONB das fichas e índice vetorial no mesmo lugar |

## 3. C4 — Componentes da API

O core é um hexágono: `adapters → application → domain`. A vertical é montada por um
`VerticalPack` e entrega catálogo, persona, intenções, regras e rotas.

```mermaid
flowchart TB
    subgraph inbound[core · adapters/inbound/http]
        rotas[FastAPI<br/>/health · /conversas · /leads · /painel · /qualidade<br/>/atendimentos · /agendamentos · /lgpd · /demo]
        webhooks[webhooks WhatsApp<br/>Meta: X-Hub-Signature-256 · Twilio: X-Twilio-Signature<br/>idempotência pelo id do provedor]
        protecao[protecao.py<br/>X-API-Key · rate limit · IP]
    end

    subgraph application[core · application — sem frameworks]
        uc[casos de uso<br/>ReceberMensagem · ProcessarTurno · EntregarMensagem<br/>ConduzirAgendamento · GerarResumoHandoff · atendimento<br/>ProgramarFollowUps · ExecutarFollowUps · LGPD · AvaliarResumo]
        consultas["consultas (read models)<br/>painel · qualidade"]
        ports{{ports<br/>LLMPort · AgenteConversacionalPort · CatalogoPort · EmbeddingPort<br/>Lead/Conversa/LeadEvento repos · AgendaPort · CRMPort · CanalMensagemPort<br/>AgendadorTurnoPort · TravaTurnoPort · LimitadorTaxaPort · RastreioPort · DadosTitularPort}}
    end

    domain["core · domain — puro<br/>Lead · Conversa · Mensagem · Qualificacao · Score · EventoLead<br/>agenda (decidir) · atendimento (estados) · followup · resumo (ancoragem)<br/>template · seguranca (injeção, promessas) · pii"]

    subgraph outbound[core · adapters/outbound]
        agente["agent — AgenteQualificador (LangGraph)<br/>roteador · extração · especialistas · agenda · atendimento · redirecionar"]
        llm[llm — OpenAI · LLMObservado]
        persist[persistence — SQLAlchemy async<br/>repos · agenda mock · CRM mock · consultas SQL · LGPD]
        canais[canais — web · WhatsApp Meta · Twilio]
        outros["turnos (debounce) · trava (advisory lock) · eventos · limite_taxa<br/>embeddings (fastembed) · observabilidade (Langfuse)"]
    end

    subgraph vertical[verticals/imobiliario — PackImobiliario]
        catalogo[catalogo — Imovel · busca híbrida · rota /imoveis]
        persona[persona — Lia, prompts versionados, promessas do segmento]
        qualif[qualificacao — fichas por intenção · scoring · critério]
        agenda_v[agenda — atribuição por região/intenção · tipos de visita]
        followup_v[followup — cadências · templates]
        resumo_v[resumo — template do resumo para o corretor]
    end

    rotas & webhooks --> protecao
    rotas & webhooks --> uc
    rotas --> consultas
    uc --> ports
    uc --> domain
    consultas --> ports
    agente & llm & persist & canais & outros -. implementam .-> ports
    catalogo -. "CatalogoImobiliario implementa CatalogoPort" .-> ports
    vertical -. "entrega ao core via VerticalPack" .-> uc
```

**Regras que o build garante** (import-linter, `.importlinter`; violação quebra o CI):

| Contrato | O que impede |
|---|---|
| Camadas globais | `(main · cli · worker) > bootstrap > (verticals · config) > core` invertido |
| Core não importa verticals | qualquer `sdr.verticals` dentro de `sdr.core` |
| Hexágono do core | `domain` importar `application`; `application` importar `adapters` |
| Vertical imobiliária | `pack > followup > agenda > (config · catalogo · persona · qualificacao · resumo)` invertido |
| Hexágono das fatias | o mesmo do core dentro de `catalogo`, `qualificacao`, `agenda` |
| Núcleos puros | domain/application importarem FastAPI, Pydantic, SQLAlchemy, LangGraph, OpenAI, httpx, Streamlit, pgvector… |
| Adapters independentes | `core.adapters.inbound` ⟂ `core.adapters.outbound` |
| Web isolado | o Streamlit importar o backend ou o banco |

Além disso, `tests/unit/core/test_core_agnostico.py` garante o vocabulário: nenhum
"imóvel", "corretor" ou "metrô" dentro de `core/`.

## 4. Core genérico × vertical

O **core** sabe fazer SDR conversacional para qualquer segmento. A **vertical** ensina o
negócio. As duas se encontram no contrato `VerticalPack` ([ADR 003](adr/003-core-multi-segmento.md)).

```mermaid
flowchart TB
    boot[[bootstrap.py<br/>VERTICAL=imobiliario]]

    subgraph core[sdr.core — NÃO conhece imóvel]
        contrato{{vertical.py<br/>VerticalPack · InfraCompartilhada · VerticalMontada}}
        generico[SDR genérico<br/>turno · grafo · qualificação · agenda · atendimento<br/>follow-up · canais · guardrails · LGPD · painel]
    end

    subgraph vert[sdr.verticals.imobiliario]
        pack[[pack.py — PackImobiliario]]
        itens[catálogo de imóveis + busca · persona Lia<br/>intenções compra/aluguel/investimento + fichas + scoring<br/>atribuição de corretor · cadências e templates · template do resumo]
    end

    boot -- "1. cria a infra compartilhada" --> generico
    boot -- "2. pack.montar(InfraCompartilhada)" --> pack
    pack -. implementa .-> contrato
    pack --> itens
    pack -- "3. VerticalMontada" --> boot
    boot -- "4. injeta no core" --> generico
```

| O core decide | A vertical declara |
|---|---|
| quando trocar de intenção, como fazer merge da ficha, o que falta perguntar | quais intenções existem, o schema de cada ficha e a prioridade dos campos |
| que só confirma agenda depois do "sim", que horários são reais | quem atende cada lead (região, intenção) e os tipos de compromisso |
| quando o follow-up é elegível, janela de 24 h, SLA | a cadência (D+1, D+3, D+7) e o texto de cada template |
| ancoragem do resumo (nada sem lastro) | as seções do resumo para o corretor |
| guardrails genéricos (injeção, desconto, garantia) | promessas típicas do segmento e a resposta segura da persona |

Segunda vertical = um pacote novo em `verticals/` implementando o mesmo `VerticalPack`,
sem tocar no core.

## 5. Um turno de ponta a ponta (do webhook ao envio)

O lead manda três mensagens seguidas no WhatsApp. O webhook responde 200 na hora; a Lia
espera ele parar de digitar (debounce), responde **uma vez** ao conjunto e a resposta sai
pela Graph API ([ADR 006](adr/006-processamento-assincrono-debounce.md),
[ADR 012](adr/012-canal-whatsapp-templates.md)).

```mermaid
sequenceDiagram
    autonumber
    participant L as Lead (WhatsApp)
    participant M as Meta Cloud API
    participant W as webhook /webhooks/whatsapp/meta
    participant RM as ReceberMensagem
    participant AG as AgendadorDebounce
    participant PT as ProcessarTurno
    participant G as AgenteQualificador (grafo)
    participant LLM as OpenAI
    participant DB as Postgres
    participant E as EntregarMensagem
    participant LF as Langfuse

    L->>M: "oi" · "quero alugar" · "no centro até 3 mil"
    M->>W: POST (X-Hub-Signature-256)
    W->>W: assinatura ✓ · rate limit por remetente · normaliza (E.164)
    W->>RM: MensagemRecebida(canal=whatsapp)
    RM->>DB: grava PENDENTE (idempotente pelo id da Meta) · cria lead
    RM->>AG: agendar(lead) — janela de 5 s, reinicia a cada mensagem (teto 20 s)
    W-->>M: 200 imediato
    Note over AG: 5 s sem mensagem nova
    AG->>PT: executar(lead)
    PT->>DB: advisory lock do lead · pendentes agregadas (CPF mascarado)
    PT->>M: "digitando…" + marca como lida (sem humano atendendo · falha só vira log)
    M->>L: ✓✓ azul e os três pontinhos
    PT->>G: responder(texto do lote, histórico)
    G->>LLM: roteador (intenção, injeção, fora do domínio, humano, agenda, opt-out)
    G->>LLM: extração (ficha da intenção) → merge e score no domínio
    G->>LLM: especialista + ferramenta buscar_imoveis (busca híbrida)
    G-->>PT: texto + qualificação + eventos
    PT->>PT: validação de saída: códigos existem? promessa indevida?<br/>(reprovou ⇒ regenera 1 vez ⇒ resposta segura)
    PT->>DB: chegou mensagem nova? (sim ⇒ descarta e reprocessa)
    PT->>DB: resposta + metadados (versão, tokens, trace) · lote PROCESSADO · eventos
    PT->>E: entregar pelo canal do lead
    E->>E: janela de 24 h aberta ⇒ texto livre (fora ⇒ template aprovado ou não envia)
    E->>M: POST /messages (Graph API)
    M->>L: resposta da Lia
    PT-->>LF: 1 rastro por turno: etapa por nó, geração por chamada ao LLM
    PT->>DB: reprograma follow-up · publica eventos (resumo para o corretor fora do turno)
```

No chat web o caminho é o mesmo a partir de `ReceberMensagem` (POST 202 + polling).
Em `ATENDIMENTO_HUMANO` o grafo nem chama o LLM: a mensagem é só registrada para a equipe.

## 6. Grafo de agentes (dentro do turno)

Cada nó tem o seu LLM (modelo configurável por nó); as **regras** ficam no domínio e na
vertical, os nós só orquestram ([ADR 005](adr/005-grafo-qualificacao-multiagente.md)).

```mermaid
flowchart TD
    START([lote do lead + estado]) --> GATE{"gate<br/>estado do atendimento"}
    GATE -- ATENDIMENTO_HUMANO --> SIL([silêncio · sem LLM])
    GATE -- AGUARDANDO_HUMANO --> ESPERA["espera<br/>quer voltar para a assistente?"]
    GATE -- "CONFIRMANDO_*" --> CONF["confirmação<br/>sim · não · ambígua"]
    GATE -- ATENDIMENTO_IA --> R["roteador (saída estruturada)<br/>intenção · confiança · humano? · agendar?<br/>fora do alcance? · opt-out? · injeção? · fora do domínio?"]
    R -- "injeção (heurística OU LLM)<br/>ou assunto alheio" --> RED["redirecionar<br/>sem ferramentas · sem extração<br/>no papel, volta ao atendimento"]
    R -- opt-out --> OPT["opt_out<br/>confirma e encerra a cadência"]
    R -- "precisa de humano" --> SH[solicitar_handoff]
    ESPERA & CONF & SH --> RAT["responder_atendimento<br/>fila · horário · retorno"]
    R -- "sem intenção" --> DESC["descoberta"]
    R -- "intenção X" --> EX["extração (schema da ficha X)<br/>→ validação da vertical + merge no domínio"]
    EX --> SC["scoring da vertical<br/>+ campos faltantes · próxima ação"]
    SC -- "não qualificado" --> ESPEC["especialista X<br/>persona + prompt X + bloco de estado"]
    SC -- "qualificado / quer agendar / já tem visita" --> AGD["agenda<br/>LLM interpreta → domínio decide<br/>horários reais · reserva só após o 'sim'"]
    AGD -- "não quer agendar agora" --> ESPEC
    AGD --> RA["responder_agenda"]
    ESPEC <-- "tool calls" --> T["ferramentas<br/>buscar_imoveis → CatalogoPort"]
    DESC <-- "tool calls" --> T
    RED & OPT & RAT & ESPEC & DESC & RA --> FIM([texto + qualificação + agenda + ação de atendimento + eventos])
```

Toda resposta carrega a **hierarquia de instruções** (`seguranca_v1`: só o sistema
instrui; lead e ferramentas são dados) e os **limites** (`limites_v1`: o que a IA faz e o
próximo passo real para o resto).

## 7. Máquina de estados do atendimento (IA × humano)

Transição fora da tabela é erro de domínio; toda passagem pede confirmação explícita, e
uma resposta ambígua gera UMA nova pergunta ([ADR 009](adr/009-atendimento-humano-handoff.md)).

```mermaid
stateDiagram-v2
    [*] --> ATENDIMENTO_IA
    ATENDIMENTO_IA --> CONFIRMANDO_HANDOFF: pede humano · frustração · fora do escopo · negociação<br/>(HandoffSolicitado)
    CONFIRMANDO_HANDOFF --> AGUARDANDO_HUMANO: sim (HandoffConfirmado)
    CONFIRMANDO_HANDOFF --> ATENDIMENTO_IA: não / ambígua 2x (HandoffRecusado)
    AGUARDANDO_HUMANO --> ATENDIMENTO_HUMANO: equipe assume pela Fila (AtendimentoHumanoIniciado)
    AGUARDANDO_HUMANO --> CONFIRMANDO_RETORNO_IA: quer voltar (RetornoIASolicitado)
    CONFIRMANDO_RETORNO_IA --> ATENDIMENTO_IA: sim, sai da fila (RetornoIAConfirmado)
    CONFIRMANDO_RETORNO_IA --> AGUARDANDO_HUMANO: não / ambígua 2x
    ATENDIMENTO_HUMANO --> ATENDIMENTO_IA: equipe devolve (AtendimentoHumanoEncerrado)
```

O grafo só **decide** a ação e prevê o novo estado. O `ProcessarTurno` recheca o estado
antes de enviar (se um humano assumiu no meio, a resposta é descartada) e aplica a ação
com compare-and-set. Fora do horário de atendimento da operação, a Lia avisa quando a
equipe volta; espera na fila acima do SLA (tempo útil) vira `HandoffSLAExcedido`.

## 8. Agenda, resumo e follow-up

### Negociação de horário ([ADR 007](adr/007-agendamento-agenda-mock.md))

```mermaid
stateDiagram-v2
    [*] --> SemProposta
    SemProposta --> Ofertado: oferece 2–3 horários reais do responsável certo
    Ofertado --> AguardandoConfirmacao: lead escolhe ("quinta à tarde", "a 2ª")
    SemProposta --> AguardandoConfirmacao: lead já indica horário
    AguardandoConfirmacao --> Ofertado: recusa · horário tomado (alternativas)
    AguardandoConfirmacao --> Agendado: "sim" ⇒ AgendaPort.reservar (idempotente)
    Agendado --> AguardandoConfirmacao: remarcar / cancelar (sempre confirma)
    Agendado --> SemProposta: cancelado
    SemProposta --> NaoInsistir: não quer agendar agora
```

### Resumo para o responsável — fora do turno ([ADR 008](adr/008-resumo-handoff-crm.md))

```mermaid
sequenceDiagram
    participant PT as ProcessarTurno
    participant P as PublicadorEventos (asyncio · produção: outbox + fila)
    participant G as GerarResumoHandoff
    participant R as Redator (LLM) + verificador de frases
    participant DB as resumos_handoff
    participant CRM as CRMPort (mock → HubSpot)

    PT->>P: eventos do turno (LeadQualificado, AgendamentoCriado, HandoffConfirmado…)
    Note over PT: o turno termina aqui
    P-->>G: gatilho
    G->>G: fatos do lead + impressão digital (nada mudou? para)
    G->>R: redigir(fatos, template da vertical)
    G->>G: ancorar: dados vêm do estado · itens só os citados · evidência literal do lead
    G->>G: lead anonimizado durante a redação? descarta (LGPD)
    G->>DB: nova versão
    G->>CRM: cria/atualiza + anexa resumo
```

### Follow-up automático ([ADR 011](adr/011-followup-worker.md))

```mermaid
flowchart LR
    PT[ProcessarTurno respondeu] -- "recomeça a cadência" --> PF[ProgramarFollowUps]
    RM[ReceberMensagem: lead falou] -- "cancela pendentes (LeadReengajado)" --> PF
    EV[eventos de agenda e handoff] -- "lembrete 24 h antes · checagem de SLA" --> PF
    PF --> Q[(followups_agendados)]
    W[[worker]] -- laço --> EX[ExecutarFollowUps]
    EX -- "SELECT … FOR UPDATE SKIP LOCKED" --> Q
    EX --> EL{elegível? domínio: opt-out,<br/>horário, janela, estado}
    EL -- não --> C[cancela / adia com motivo]
    EL -- sim --> RED[redator: mensagem curta + item novo do catálogo]
    RED --> ENT[EntregarMensagem: texto livre na janela · template fora dela]
```

## 9. Busca híbrida (RAG) e estado de qualificação

```mermaid
sequenceDiagram
    participant A as Agente (tool buscar_imoveis) ou POST /imoveis/busca
    participant U as BuscarImoveis (vertical)
    participant I as InterpretadorRegras
    participant E as Embeddings locais (fastembed, ONNX)
    participant DB as Postgres + pgvector

    A->>U: texto + filtros (zona em "bairros" vira zona)
    U->>I: interpretar(texto) → critérios inferidos
    Note over U: explícitos sobrescrevem inferidos
    U->>E: vetor da consulta (384 dimensões)
    U->>DB: WHERE filtros estruturados ORDER BY embedding <=> vetor LIMIT k (HNSW)
    DB-->>U: imóveis + similaridade
    U-->>A: itens com resumo e atributos (o agente só cita códigos que voltaram daqui)
```

O estado do lead (`GET /leads/{id}`) vem do agregado `Qualificacao`: intenção atual,
uma ficha **JSONB por intenção** (trocar de intenção não apaga nada), campos faltantes
na prioridade da vertical, score com motivos, próxima ação e a trilha completa de
`lead_eventos`, que alimenta o painel, o funil e a página de Qualidade
([ADR 014](adr/014-dashboard-consultas.md), [ADR 017](adr/017-qualidade-da-ia.md)).

## 10. Segurança em camadas

```mermaid
flowchart LR
    entrada[mensagem] --> borda["borda HTTP<br/>assinatura do webhook · rate limit<br/>tamanho máximo · X-API-Key nas rotas da equipe"]
    borda --> turno["turno<br/>CPF fora do LLM"]
    turno --> grafo["grafo<br/>hierarquia de instruções · heurística + roteador de injeção<br/>redirecionar no papel · sem ferramenta de dados de terceiros"]
    grafo --> saida["validação de saída<br/>item existe · sem promessa indevida<br/>regenera 1x · resposta segura"]
    saida --> registros["registros<br/>PII mascarada em log, traceback, Langfuse, exportação<br/>eventos auditáveis · LGPD: exportar/anonimizar"]
```

Ameaças, controles e riscos residuais: [docs/seguranca.md](seguranca.md) ([ADR 015](adr/015-seguranca-guardrails.md)).

## 11. Implantação: local e nuvem

```mermaid
flowchart LR
    subgraph local[docker compose]
        lw[web :8501] --> la[api :8000<br/>migra no start] --> ldb[(db :5433)]
        lwk[worker] --> ldb
    end
    subgraph nuvem[Railway — .railway/railway.ts]
        rw[web<br/>healthcheck /_stcore/health] -- HTTPS --> ra[api · 1 réplica<br/>pré-deploy: alembic + seed<br/>healthcheck /health]
        ra --> rdb[(pgvector + volume)]
        rwk[worker<br/>reinício ALWAYS] --> rdb
    end
    ci[GitHub Actions<br/>ruff · mypy · import-linter<br/>alembic check · pytest] -. push .-> nuvem
```

Passo a passo e variáveis em [docs/deploy.md](deploy.md) ([ADR 016](adr/016-deploy-railway.md)).

## 12. Índice dos ADRs

| ADR | Decisão |
|---|---|
| [001](adr/001-arquitetura-hexagonal.md) | Arquitetura hexagonal (Ports & Adapters) |
| [002](adr/002-busca-hibrida-embeddings-locais.md) | Busca híbrida: filtros estruturados + embeddings locais (fastembed) em pgvector |
| [003](adr/003-core-multi-segmento.md) | Core de SDR genérico × verticais (`VerticalPack`) |
| [004](adr/004-agente-llm-memoria.md) | LLM só por port, LangGraph como orquestrador, memória no banco |
| [005](adr/005-grafo-qualificacao-multiagente.md) | Grafo de qualificação: roteador, extração, scoring, especialistas |
| [006](adr/006-processamento-assincrono-debounce.md) | Turnos assíncronos com debounce e advisory lock |
| [007](adr/007-agendamento-agenda-mock.md) | Agendamento: domínio decide, confirmação antes de reservar, agenda mock |
| [008](adr/008-resumo-handoff-crm.md) | Resumo para o responsável fora do turno, ancorado; CRMPort |
| [009](adr/009-atendimento-humano-handoff.md) | Atendimento humano: máquina de estados, gate, compare-and-set |
| [010](adr/010-limites-do-assistente.md) | Limites do assistente: honestidade + próximo passo real |
| [011](adr/011-followup-worker.md) | Follow-up: fila SKIP LOCKED, worker, cadência da vertical |
| [012](adr/012-canal-whatsapp-templates.md) | Canal WhatsApp e templates (janela de 24 h) |
| [013](adr/013-observabilidade-langfuse.md) | Observabilidade com Langfuse (rastro por turno) |
| [014](adr/014-dashboard-consultas.md) | Dashboard: consultas separadas dos comandos, front só via API |
| [015](adr/015-seguranca-guardrails.md) | Segurança e guardrails em camadas |
| [016](adr/016-deploy-railway.md) | Deploy na Railway, imagens multi-stage, CI |
| [017](adr/017-qualidade-da-ia.md) | Qualidade da IA: métricas online por versão + eval offline na API |
