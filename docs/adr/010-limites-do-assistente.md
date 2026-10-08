# ADR 010 — Limites do assistente: honestidade e próximo passo real

- **Status:** Aceita
- **Data:** 2026-10-07
- **Relacionados:** [ADR 005](005-grafo-qualificacao-multiagente.md),
  [ADR 007](007-agendamento-agenda-mock.md), [ADR 009](009-atendimento-humano-handoff.md)

## Contexto

Num teste da PO, o lead perguntou "tem fotos?". A Lia respondeu "Tenho sim, mas só posso
enviar por um corretor. Quer que eu peça para alguém te enviar?". A resposta inventou duas
coisas:

- a **informação** (a base não tem fotos);
- uma **capacidade** do sistema (não existe ação de "pedir para alguém mandar").

O lead disse "sim", mas não havia caminho para isso: o lead ainda não estava qualificado,
então o grafo voltou ao roteiro de perguntas e a conversa "se perdeu". A persona ainda
incentivava esse comportamento ("se não souber, diga que vai confirmar com o corretor").

## Decisão

1. **Limites explícitos, do core, em toda resposta** (`prompts/limites_v1.md`, montado em
   `_contexto`):
   - **O que o assistente consegue:** conversar, usar só o que as ferramentas devolveram e
     o que foi dito, marcar horário (quando a intenção tem `TipoAgendamento`) e transferir
     para a equipe.
   - **O que não consegue:** enviar fotos, vídeos ou arquivos, ligar, "pedir para alguém
     mandar depois", prometer retorno.
   - **Fora disso:** honestidade (nunca "tenho, mas…") e a oferta do **próximo passo que
     existe**, montada pelo core a partir do tipo de agendamento da intenção (ex.: "marcar
     visita aos imóveis com um corretor").
2. **O roteador ganha dois sinais**, avaliados sobre a última fala (o mesmo cuidado do
   ADR 009):
   - `quer_agendar`: o lead pede para marcar ou aceita a oferta de marcar. Isso abre o nó
     de agenda **mesmo antes de qualificar**, e o "sim" à oferta leva aos horários reais.
   - `fora_do_alcance`: o lead pede foto, vídeo, arquivo, documento ou link. Nesse turno, o
     bloco do especialista **não traz o "próximo dado a descobrir"**. Só a instrução no
     prompt não bastou: o modelo emendava "quantos quartos?" na oferta, e o "sim" ficava
     ambíguo.
3. **A vertical declara o que não tem:**
   - persona `lia_v2`: fotos, planta, documentação, proposta, disponibilidade de fim de
     semana, com a sugestão de conhecer o imóvel com um corretor;
   - `descoberta_v2`: registra todas as pistas do lead (o exemplo antigo sem orçamento era
     copiado ao pé da letra).

## Consequências

- Um cenário com LLM real (`07_pedido_fora_da_base_vira_visita`) cobre o caso: resposta
  sem "tenho sim", "vou pedir", "te envio" nem segunda pergunta; o "sim" vira agendamento.
- Agendar antes de qualificar aceita uma ficha incompleta. A regra de atribuição da
  vertical lida com região desconhecida (ADR 007), e o resumo marca os campos faltantes
  como "não informado".
