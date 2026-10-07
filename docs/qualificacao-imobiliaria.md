# Qualificação — vertical imobiliária

Este documento descreve as regras de negócio da qualificação imobiliária. O código fica em:
- [regras.py](../src/sdr/verticals/imobiliario/qualificacao/domain/regras.py): campos,
  scoring e critério de qualificado;
- [fichas.py](../src/sdr/verticals/imobiliario/qualificacao/adapters/fichas.py): schemas e
  descrições dos campos;
- [prompts dos especialistas](../src/sdr/verticals/imobiliario/persona/prompts/especialistas/).

O fluxo genérico (roteador → extração → scoring → especialista) está no
[ADR 005](adr/005-grafo-qualificacao-multiagente.md).

## Intenções e fichas (campos na ordem em que a Lia pergunta)

| Intenção | Campos, por prioridade | Essenciais para qualificar | Próxima ação |
|---|---|---|---|
| compra | regiao · preco_max · quartos · tipo_imovel · urgencia · forma_pagamento · vagas | regiao, preco_max, quartos, urgencia, forma_pagamento | `agendar_visita` |
| aluguel | regiao · aluguel_max (com condomínio) · quartos · prazo_mudanca · tipo_garantia · aceita_pets | regiao, aluguel_max, quartos, prazo_mudanca, tipo_garantia | `agendar_visita` |
| investimento | ticket · objetivo · expectativa_retorno · prazo_investimento · experiencia_previa | ticket, objetivo, prazo_investimento, experiencia_previa | `encaminhar_especialista` |

- **Quando a "indefinida" entra:** é a intenção do core enquanto o objetivo não está claro.
  Nessa fase a Lia só pergunta se a pessoa quer comprar, alugar ou investir.
- **Quando a Lia mostra imóveis:** assim que a ficha tem os campos abaixo, a Lia busca no
  catálogo e apresenta até 3 opções reais:
  - compra: região + preço;
  - aluguel: região + aluguel;
  - investimento: ticket + objetivo.
- **Troca de intenção:** a ficha antiga é preservada, e a nova herda `regiao` e `quartos`.
  Isso acontece, por exemplo, quando a pessoa começa no aluguel e passa para compra.

## Scoring (0–100)

| Critério | compra | aluguel | investimento |
|---|---|---|---|
| Orçamento definido | +25 (preco_max) | +25 (aluguel_max) | +25 (ticket) |
| Clareza | +15 (região) | +15 (região) | +15 (objetivo) |
| Urgência / prazo | imediata ou até 3 meses +20 · 3–6 meses +10 · 6–12 meses +5 | imediata ou até 1 mês +20 · 1–3 meses +10 · mais de 3 meses +5 | horizonte definido +10 · expectativa de retorno +10 |
| Forma de pagamento / garantia / experiência | pagamento definido +15 | garantia definida +15 ("não sei" = 0) | já possui imóveis ou é experiente +15 · primeiro investimento +5 |
| Completude da ficha | até +25 (proporcional aos campos preenchidos) | até +25 | até +25 |

- **Classificação:** **quente** a partir de 70 pontos, **morno** de 40 a 69 e **frio**
  abaixo de 40.
- **`score_motivos`:** cada regra gera uma linha legível para o corretor. Por exemplo:
  `+25 orçamento definido (até R$ 800.000)`, `+0 forma de pagamento não informada`,
  `+7 completude da ficha (2/7 campos)`.
- **Qualificação:** quando todos os essenciais da intenção estão preenchidos, o lead é
  qualificado. Nesse momento:
  - o evento `LeadQualificado` é emitido uma única vez por intenção;
  - a `proxima_acao` é marcada;
  - a Lia passa a conduzir para essa ação, sem combinar data nem horário (o agendamento
    é do Dia 3).
