# ADR 007 — Agendamento: AgendaPort, mock em Postgres e nó de agenda no grafo

- **Status:** Aceita
- **Data:** 2026-10-07
- **Relacionados:** [ADR 003](003-core-multi-segmento.md) (core × vertical),
  [ADR 005](005-grafo-qualificacao-multiagente.md) (grafo de qualificação),
  [ADR 006](006-processamento-assincrono-debounce.md) (turnos assíncronos)

## Contexto

No Dia 3, depois de qualificar, a Lia precisa marcar o próximo passo comercial com uma
pessoa da equipe: na imobiliária, uma visita com um corretor (compra e aluguel) ou uma
reunião com o especialista em investimentos (online ou no escritório). O lead fala em
linguagem natural ("quinta à tarde", "amanhã cedo", "depois das 18h"), e o agendamento
precisa ser confiável:

- nenhum horário inventado;
- nada reservado sem confirmação explícita;
- dois leads nunca no mesmo horário;
- reprocessar um turno (ADR 006) não pode reservar duas vezes.

Tudo isso precisa servir a outros segmentos: o core não pode saber o que é uma visita nem
quem é um corretor.

## Decisão

1. **Conceitos genéricos no domínio do core** (`core/domain/agenda.py`):
   - `Responsavel`: o `titulo` e as `especialidades` vêm da vertical;
   - `Slot`, `Agendamento` e `TipoAgendamento`: o tipo tem um rótulo e modalidades,
     declarados pela vertical;
   - `PreferenciaHorario`: dias da semana, data, "daqui a N dias", período, depois/antes de
     uma hora, hora exata;
   - a negociação (`NegociacaoAgenda`): opções oferecidas e uma proposta aguardando
     confirmação;
   - `decidir(...)`: decide o próximo passo da negociação. É uma função pura, testada com
     relógio fake.
2. **`AgendaPort` no core:** `listar_disponibilidade`, `reservar` (idempotente por chave),
   `remarcar`, `cancelar` e `agendamento_ativo`. O **mock** (`AgendaPostgres`) usa as
   tabelas `responsaveis`, `slots_agenda` e `agendamentos`:
   - a grade cobre os próximos 10 dias úteis, das 9h às 20h;
   - cerca de 1 em cada 4 slots já vem ocupado por "outros compromissos";
   - a grade é gerada no start da API e no `seed`.

   **Em produção, o adapter troca por Google Calendar ou Outlook:** free/busy na
   disponibilidade e eventos para reservar, remarcar e cancelar. A tabela `agendamentos`
   continua sendo o registro do sistema.
3. **O `VerticalPack` ganha três itens:**
   - `regra_atribuicao`: intenção e ficha definem quem atende, em ordem de preferência;
   - `tipos_agendamento`: o que se agenda em cada intenção;
   - `responsaveis_iniciais`: o seed do mock.

   O horário de atendimento e o fuso são da **operação** (`FUSO_OPERACAO`), não da vertical.
4. **Nó de agenda no grafo.** O caminho é `scoring → agenda → responder_agenda`. Ele é
   acionado quando o lead está qualificado numa intenção com `TipoAgendamento`, ou quando
   já existe uma negociação em curso. O LLM nunca escolhe horário:
   - **interpreta** a fala em saída estruturada: a ação (`confirmar`, `preferencia`,
     `remarcar`...) e a preferência estruturada;
   - **redige** a resposta a partir de um bloco interno com os horários REAIS já
     formatados ("quinta (08/10) às 14h com Rafael Souza (corretor da zona sul)").

   O caso de uso `ConduzirAgendamento` busca os slots livres dos responsáveis aptos e chama
   `decidir`. Só executa no `AgendaPort` quando há proposta pendente **e** o lead confirmou.
   Se o lead não quer agendar agora, o grafo volta ao especialista, que não insiste.
5. **Datas relativas sempre no fuso da operação** (America/Sao_Paulo). "Quinta" vale a
   próxima quinta e a seguinte: se esta estiver lotada, serve a outra. Nenhum horário é
   oferecido com antecedência menor que `AGENDA_ANTECEDENCIA_HORAS`, nem além de
   `AGENDA_JANELA_DIAS`.
6. **Concorrência e idempotência:**
   - ocupar um slot é um `UPDATE ... WHERE agendamento_id IS NULL` atômico; quem perde
     recebe `SlotIndisponivelError`, e a Lia oferece alternativas;
   - a chave de idempotência é `lead:slot`: o mesmo "sim" reprocessado devolve o mesmo
     agendamento;
   - remarcar para o slot atual e cancelar o que já está cancelado não fazem nada.
7. **Eventos:** `AgendamentoCriado`, `AgendamentoRemarcado` e `AgendamentoCancelado` vão
   para `lead_eventos`. Eles são emitidos só pela execução bem-sucedida e gravados pelo
   turno junto com a negociação (coluna `leads.agenda`, JSONB).

## Consequências

- O core continua agnóstico: `test_core_agnostico` também barra "visita".
- Um turno com agenda faz uma chamada estruturada a mais (a interpretação, com o modelo de
  extração).
- A reserva acontece dentro do turno, antes de a resposta ser enviada. Se o turno for
  descartado para reprocessar (ADR 006), a idempotência garante que o "sim" repetido não
  reserve de novo. Se o turno falhar depois de reservar, o `agendamento_ativo` reconcilia
  no turno seguinte, mas o evento daquele turno se perde (raro e aceito na POC).
- A visita não pergunta qual imóvel: o agendamento leva os imóveis citados na conversa,
  como contexto para o corretor e para o resumo da Etapa B.
