# ADR 006 — Processamento assíncrono de turnos com agregação de mensagens (debounce)

- **Status:** Aceita
- **Data:** 2026-10-06
- **Relacionados:** [ADR 004](004-agente-llm-memoria.md), [ADR 005](005-grafo-qualificacao-multiagente.md)

> O pedido original sugeria `003-processamento-assincrono-debounce.md`. Como o 003 já
> registra a separação core × vertical, esta decisão ficou com o número 006.

## Contexto

Leads escrevem como no WhatsApp: "procuro apê" / "zona sul" / "até 800 mil", em várias
mensagens curtas seguidas. No fluxo síncrono (uma mensagem gera uma resposta), a Lia
respondia à primeira mensagem antes de a pessoa terminar, perguntava o que viria na
seguinte e gastava 3 ou 4 chamadas ao LLM por mensagem.

Além disso, o WhatsApp (Dia 4) é assíncrono por natureza: o webhook precisa responder
rápido, e a resposta vai por outro canal (API de envio).

## Decisão

1. **Há dois casos de uso no core:**
   - **`ReceberMensagem`** grava a mensagem como `pendente`, chama
     `AgendadorTurnoPort.agendar(lead)` e retorna. No chat web, isso é **HTTP 202**.
   - **`ProcessarTurno`** agrega as pendentes do lead em ordem (separadas por quebra de
     linha), executa o grafo **uma vez** e só então persiste:
     - grava a resposta;
     - marca o lote como `processada`;
     - grava a qualificação e os eventos;
     - entrega pelo **`CanalMensagemPort`** do canal do lead.
2. **Debounce por lead** (`AgendadorTurnoPort`): cada mensagem reinicia a janela de
   silêncio (`DEBOUNCE_SEGUNDOS`, padrão 5). Há um teto contado desde a primeira mensagem
   do ciclo (`DEBOUNCE_MAX_SEGUNDOS`, padrão 20), para quem nunca para de digitar.
3. **Mensagem que chega durante o processamento:**
   - **Se a resposta ainda não foi enviada**, ela é descartada e o turno é reprocessado
     com todas as pendentes. Há um teto de 3 reprocessamentos; depois disso, a resposta
     sai e o restante forma o próximo turno.
   - **Se a resposta já foi enviada**, as novas mensagens formam o próximo turno (o
     agendador abre um novo ciclo).
4. **Trava por lead com advisory lock do Postgres** (`TravaTurnoPort`):
   `pg_try_advisory_lock` sem bloquear. Se outro processo detém a trava, o turno é
   reagendado. A trava vale entre processos e réplicas, então nunca há dois turnos do
   mesmo lead em paralelo.
5. **A mensagem ganhou status:** `pendente`, `processada` ou `falha` (do lead) e
   `enviada` (do agente).
   - O histórico entregue ao agente exclui as pendentes, que entram como o texto do turno.
   - Uma falha no turno marca o lote como `falha`. Essas mensagens continuam no histórico,
     e a interface avisa que ficaram sem resposta.
   - No start da API, `RecuperarTurnosPendentes` reagenda os leads que ainda têm
     pendentes.
6. **Chat web:** `POST /conversas/mensagens` devolve 202 e o front faz polling de
   `GET /conversas/{lead_id}/mensagens`, que informa `processando` (há pendentes). O
   Streamlit usa `st.fragment(run_every=1.5s)` e mostra "Lia está digitando…" enquanto
   houver turno. O `CanalWeb` não transporta nada, porque a resposta já está persistida.
   O `CanalMensagemPort` já prevê `enviar_template`, para mensagens fora da janela de 24h
   do WhatsApp.

## POC × produção

| Peça | POC (hoje) | Produção (troca só o adapter) |
|---|---|---|
| `AgendadorTurnoPort` | `AgendadorDebounce`: asyncio in-process, estado em memória | Fila com atraso e chave por lead: Redis (ZSET/streams), Celery ou RQ com countdown, SQS com delay. Workers separados da API |
| `TravaTurnoPort` | Advisory lock do Postgres | Igual, ou lock no Redis (SET NX PX) |
| `CanalMensagemPort` | `CanalWeb` (sem transporte) | `CanalWhatsApp` (Twilio / Cloud API), Dia 4 |

**Limitação aceita na POC:** o agendador vive no processo da API, que portanto roda numa
única réplica. Se o processo morrer, as pendências são recuperadas no próximo start. Com
várias réplicas, a trava continua garantindo a exclusão, mas o debounce de cada lead
ficaria dividido entre processos. Por isso a produção usa a fila.

## Consequências

- **Uma resposta por "fala completa" do lead.** Isso dá menos ruído, contexto melhor
  para a extração da ficha e menos chamadas ao LLM.
- **Latência:** a primeira resposta chega pelo menos `DEBOUNCE_SEGUNDOS` depois da última
  mensagem, o que é coerente com o tempo humano de WhatsApp.
- **A conexão da trava fica reservada durante o turno**, incluindo as chamadas ao LLM. O
  pool do banco precisa comportar os turnos simultâneos.
- **O WhatsApp (Dia 4) só precisa de um router de webhook** que chame `ReceberMensagem`, e
  de um `CanalWhatsApp`.
- **Os testes do agendador usam um relógio fake** (sem `sleep` real), e a trava é testada
  contra o Postgres real.

## Alternativas consideradas

- **Debounce no front (esperar o usuário parar de digitar):** não serve para o WhatsApp,
  e o servidor continuaria sem proteção contra concorrência.
- **Responder a cada mensagem e "editar" depois:** não existe no WhatsApp e confunde o lead.
- **Lock em memória (`asyncio.Lock`):** não protege entre processos e réplicas.
