# ADR 014 — Dashboard: consultas (read models) separadas dos comandos, front só via API

- **Status:** Aceita
- **Data:** 2026-10-10
- **Relacionados:** [ADR 001](001-arquitetura-hexagonal.md), [ADR 005](005-grafo-qualificacao-multiagente.md),
  [ADR 013](013-observabilidade-langfuse.md)

## Contexto

A operação precisa acompanhar o SDR: KPIs, funil, distribuição por intenção/score/canal,
fila do humano, agenda dos responsáveis e follow-ups — por período, atualizando sozinho.
Os casos de uso existentes são comandos sobre UM lead; agregar via repositórios de
domínio (carregar leads um a um) seria lento e misturaria leitura de painel com regra
de negócio.

## Decisão

1. **Consultas separadas dos comandos:** `core/application/consultas/painel.py` define os
   read models (dataclasses puras), o port `ConsultasPainelPort` e o caso de uso
   `ConsultarPainel` (valida período, monta o funil, calcula medianas). Nada ali altera
   estado.
2. **Agregação no banco:** `ConsultasPainelSql` lê `lead_eventos` (a trilha de tudo o que
   aconteceu) e as tabelas existentes (leads, mensagens, agendamentos,
   followups_agendados) com SQL de leitura. Sem tabelas novas de projeção: o volume da
   POC não pede; se pedir, troca-se o adapter por projeções materializadas.
3. **Definições:**
   - **Coorte:** funil e distribuições olham os leads CRIADOS no período; cada lead conta
     até a etapa mais avançada a que chegou (novo → com intenção → qualificado →
     agendado), e quem chegou a uma etapa conta nas anteriores — o funil nunca sobe.
   - **Tempo até a 1ª resposta:** 1ª mensagem do assistente − 1ª do lead (inclui o
     debounce). **Até qualificar:** `LeadQualificado` − criação do lead. Medianas.
   - **Handoffs, SLA e follow-ups:** eventos que OCORRERAM no período.
4. **Rotas** `/painel/visao-geral`, `/painel/leads` (filtros: canal, intenção, estado de
   atendimento, classificação, faixa de score, demo) e `/painel/followups`. Fila, agenda e
   detalhe do lead reaproveitam as rotas existentes (com `sla_excedido` na fila e
   `lead_id` em `/agendamentos`).
5. **Front multipágina (Streamlit), só via API:** `web/app.py` monta a navegação; cada
   página em `web/paginas/` (Visão geral, Leads com detalhe, Fila, Agenda, Follow-ups,
   Chat de demonstração); peças comuns em `web/comum.py`. Atualização periódica por
   fragments; tema em `.streamlit/config.toml`. Gráficos de UMA série (barras
   horizontais, uma cor, valor na ponta, tooltip) — sem legenda nem eixo duplo.
6. **Dados de demonstração:** `scripts/seed_demo.py` gera ~40 leads coerentes nos últimos
   14 dias (intenções, scores, canais, agendamentos, handoffs, follow-ups, reengajamento,
   opt-outs), com `leads.demo = true` (migration 0012); `--remover` apaga tudo em ordem
   (agendamentos → slots ocupados → leads, com cascata).

## Consequências

- O dashboard não toca no banco e não depende de LangGraph/LLM: só da API.
- Os números refletem a trilha de eventos; evento novo relevante para a operação entra
  no SQL das consultas, não nos casos de uso.
- Leads de testes com LLM também aparecem (são leads reais do ambiente); o filtro
  "Origem" separa reais × demo.
