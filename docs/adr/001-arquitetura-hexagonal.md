# ADR 001 — Arquitetura hexagonal (Ports & Adapters)

- **Status:** Aceita
- **Data:** 2026-10-06

## Contexto

O Agente SDR começa como POC de 5 dias (Tech Challenge FIAP — Fase 5), desenvolvida por uma
pessoa, mas vai evoluir para produção. Várias peças são provisórias por natureza:

- **Canais:** hoje chat web; depois WhatsApp (Twilio Sandbox e, em produção, provavelmente a
  API oficial do WhatsApp Business).
- **LLM e orquestração:** Anthropic + LangGraph hoje; modelo, provedor e framework de agentes
  mudam rápido.
- **Integrações de negócio:** agenda de corretores e CRM começam como mock.
- **Embeddings e busca vetorial:** modelo local + pgvector; pode virar serviço dedicado.

O objetivo explícito é **zero retrabalho**: na evolução, trocar adapters, nunca reescrever as
regras de negócio.

## Decisão

Adotar arquitetura hexagonal:

- `domain/` — entidades e value objects puros (dataclasses), sem I/O.
- `application/ports/` — interfaces como `typing.Protocol` para tudo que é externo (LLM,
  embeddings, repositórios, agenda, CRM, canal de saída, agente conversacional).
- `application/use_cases/` — um caso de uso por arquivo, dependendo só de ports e domínio.
- `application/dto/` — `MensagemRecebida` agnóstica de canal: web e WhatsApp entram pelo mesmo
  caso de uso.
- `adapters/inbound` (FastAPI) e `adapters/outbound` (SQLAlchemy, pgvector, Anthropic,
  LangGraph, embeddings) implementam/consomem os ports.
- `bootstrap.py` é o **único** composition root; a escolha de adapter é feita por variável de
  ambiente (ex.: `AGENDA_PROVIDER=mock`).
- O front Streamlit é um cliente da API HTTP, como qualquer outro.

A regra é **imposta por ferramenta**, não por disciplina: o import-linter tem quatro contratos
(camadas, núcleo puro, adapters independentes, web isolado) e roda no `make check`.

Escolhas complementares:

- `Protocol` (tipagem estrutural) em vez de ABCs: adapters e fakes não precisam herdar nada, e o
  mypy strict valida a conformidade.
- Pydantic fica fora do núcleo: validação/serialização é preocupação de borda (HTTP, config).
- Acesso a dados assíncrono (SQLAlchemy 2 async + psycopg 3), coerente com FastAPI.

## Consequências

**Positivas**

- Trocar Twilio, Anthropic, LangGraph, agenda mock ou banco vetorial = novo adapter + uma
  linha no bootstrap.
- Casos de uso testáveis em milissegundos com fakes dos ports (sem banco, sem LLM).
- WhatsApp chega no Dia 4 sem tocar no núcleo: só um router de webhook que normaliza para
  `MensagemRecebida`.
- Violações de camada são detectadas no CI, não em code review.

**Negativas / custos**

- Mais arquivos e indireção do que um FastAPI "flat" — até o `/health` passa por port e use
  case. Aceito conscientemente: o custo é pequeno e a regra fica sem exceções.
- Mapeamento entre entidades de domínio e modelos ORM é explícito (sem usar o ORM como domínio).
- A lista de pacotes proibidos no núcleo precisa ser atualizada quando entra uma nova
  dependência de infraestrutura (langgraph, anthropic, twilio...).

## Alternativas consideradas

- **Monólito FastAPI em camadas simples (routers → services → models ORM):** mais rápido no
  Dia 1, mas acopla regra de negócio a SQLAlchemy/LangGraph e torna a troca de canal/LLM uma
  reescrita.
- **Clean Architecture completa (entities/use cases/interface adapters/frameworks com
  presenters):** mais cerimônia do que o necessário; o hexágono entrega o mesmo isolamento com
  menos camadas.
