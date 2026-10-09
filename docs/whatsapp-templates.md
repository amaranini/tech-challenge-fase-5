# Templates do WhatsApp — textos para aprovação na Meta

> Gerado por `python -m sdr.cli templates-doc` a partir dos templates lógicos da vertical
> `imobiliario`. Não edite à mão: mude o template na vertical e gere de novo.

Fora da janela de 24h desde a última mensagem do lead, o WhatsApp só aceita mensagem
iniciada pela empresa com **template aprovado**. Para cada template abaixo:

1. Crie o template no provedor (Twilio: *Content Template Builder*, tipo *text*; ou no
   WhatsApp Manager) com o **nome**, a **categoria**, o **idioma** e o **corpo numerado**.
2. Use os **exemplos** da tabela como valores de amostra das variáveis.
3. Aprovado, mapeie o nome lógico → template no `.env` da operação
   (`WHATSAPP_TEMPLATES`, JSON), mantendo a ordem das variáveis do corpo numerado.

Enquanto um nome lógico não estiver mapeado, nada sai como texto livre fora da janela: a
mensagem fica como não enviada e o evento `EnvioTemplateIndisponivel` é registrado.

Regras do validador (core): toda variável é uma linha só, sem tabulação, sem 4+ espaços
seguidos, não vazia e com no máximo 200 caracteres (ou o limite da variável). Valor
recusado ou dado ausente ⇒ valor padrão. Variáveis *gancho* são escritas pelo LLM e
passam pelo mesmo validador.

## `imob_retomada_descoberta`

- **Categoria:** MARKETING
- **Idioma:** pt_BR
- **Usado em:** follow-up descoberta, etapa 1

**Corpo para submissão (numerado):**

```text
Oi, {{1}}! Aqui é a Lia, da imobiliária. Você ainda está procurando imóvel? Me conta se é para comprar, alugar ou investir que eu te ajudo por aqui.
```

**Texto de referência (variáveis nomeadas):**

```text
Oi, {{primeiro_nome}}! Aqui é a Lia, da imobiliária. Você ainda está procurando imóvel? Me conta se é para comprar, alugar ou investir que eu te ajudo por aqui.
```

**Exemplo:** Oi, Ana! Aqui é a Lia, da imobiliária. Você ainda está procurando imóvel? Me conta se é para comprar, alugar ou investir que eu te ajudo por aqui.

| Nº | Variável | Regra de preenchimento | Padrão (fallback) | Exemplo | Limite |
|---|---|---|---|---|---|
| {{1}} | `primeiro_nome` | Primeiro nome do lead (perfil do WhatsApp); sem nome, uma saudação neutra. | tudo bem | Ana | 40 |

## `imob_retomada_regiao`

- **Categoria:** MARKETING
- **Idioma:** pt_BR
- **Usado em:** follow-up descoberta, etapa 2

**Corpo para submissão (numerado):**

```text
Oi, {{1}}! A Lia de novo. {{2}} Se quiser, me diga em qual região você procura que eu já separo algumas opções.
```

**Texto de referência (variáveis nomeadas):**

```text
Oi, {{primeiro_nome}}! A Lia de novo. {{gancho}} Se quiser, me diga em qual região você procura que eu já separo algumas opções.
```

**Exemplo:** Oi, Ana! A Lia de novo. Lembrei de você porque entraram opções perto do metrô. Se quiser, me diga em qual região você procura que eu já separo algumas opções.

| Nº | Variável | Regra de preenchimento | Padrão (fallback) | Exemplo | Limite |
|---|---|---|---|---|---|
| {{1}} | `primeiro_nome` | Primeiro nome do lead (perfil do WhatsApp); sem nome, uma saudação neutra. | tudo bem | Ana | 40 |
| {{2}} | `gancho` | Uma frase curta (LLM) que retoma algo concreto da conversa, sem pressão e sem inventar; termina com ponto. _(gancho — LLM)_ | Separei um tempinho para continuar te ajudando. | Lembrei de você porque entraram opções perto do metrô. | 120 |

## `imob_encerramento_busca`

- **Categoria:** MARKETING
- **Idioma:** pt_BR
- **Usado em:** follow-up descoberta, etapa 3; follow-up qualificacao, etapa 3; follow-up qualificado, etapa 3

**Corpo para submissão (numerado):**

```text
Oi, {{1}}! Vou parar de te mandar mensagens por aqui, tudo bem? Quando quiser retomar a busca, é só me chamar que eu continuo de onde paramos.
```

**Texto de referência (variáveis nomeadas):**

```text
Oi, {{primeiro_nome}}! Vou parar de te mandar mensagens por aqui, tudo bem? Quando quiser retomar a busca, é só me chamar que eu continuo de onde paramos.
```

**Exemplo:** Oi, Ana! Vou parar de te mandar mensagens por aqui, tudo bem? Quando quiser retomar a busca, é só me chamar que eu continuo de onde paramos.

| Nº | Variável | Regra de preenchimento | Padrão (fallback) | Exemplo | Limite |
|---|---|---|---|---|---|
| {{1}} | `primeiro_nome` | Primeiro nome do lead (perfil do WhatsApp); sem nome, uma saudação neutra. | tudo bem | Ana | 40 |

## `imob_retomada_busca`

- **Categoria:** MARKETING
- **Idioma:** pt_BR
- **Usado em:** follow-up qualificacao, etapa 1

**Corpo para submissão (numerado):**

```text
Oi, {{1}}! Aqui é a Lia, da imobiliária, sobre a sua busca: {{2}}. {{3}} Quer continuar de onde paramos?
```

**Texto de referência (variáveis nomeadas):**

```text
Oi, {{primeiro_nome}}! Aqui é a Lia, da imobiliária, sobre a sua busca: {{busca}}. {{gancho}} Quer continuar de onde paramos?
```

**Exemplo:** Oi, Ana! Aqui é a Lia, da imobiliária, sobre a sua busca: apartamento de 2 quartos em Pinheiros. Lembrei de você porque entraram opções perto do metrô. Quer continuar de onde paramos?

| Nº | Variável | Regra de preenchimento | Padrão (fallback) | Exemplo | Limite |
|---|---|---|---|---|---|
| {{1}} | `primeiro_nome` | Primeiro nome do lead (perfil do WhatsApp); sem nome, uma saudação neutra. | tudo bem | Ana | 40 |
| {{2}} | `busca` | Resumo da busca a partir da ficha: tipo de imóvel, quartos e região (ex.: "apartamento de 2 quartos em Pinheiros"). | o seu novo imóvel | apartamento de 2 quartos em Pinheiros | 80 |
| {{3}} | `gancho` | Uma frase curta (LLM) que retoma algo concreto da conversa, sem pressão e sem inventar; termina com ponto. _(gancho — LLM)_ | Separei um tempinho para continuar te ajudando. | Lembrei de você porque entraram opções perto do metrô. | 120 |

## `imob_novidade_imovel`

- **Categoria:** MARKETING
- **Idioma:** pt_BR
- **Usado em:** follow-up qualificacao, etapa 2; follow-up qualificado, etapa 2

**Corpo para submissão (numerado):**

```text
Oi, {{1}}! Apareceu uma opção que combina com a sua busca ({{2}}): {{3}}. Quer que eu te conte mais?
```

**Texto de referência (variáveis nomeadas):**

```text
Oi, {{primeiro_nome}}! Apareceu uma opção que combina com a sua busca ({{busca}}): {{imovel_novo}}. Quer que eu te conte mais?
```

**Exemplo:** Oi, Ana! Apareceu uma opção que combina com a sua busca (apartamento de 2 quartos em Pinheiros): IMV-012, apartamento de 2 quartos na Vila Mariana. Quer que eu te conte mais?

| Nº | Variável | Regra de preenchimento | Padrão (fallback) | Exemplo | Limite |
|---|---|---|---|---|---|
| {{1}} | `primeiro_nome` | Primeiro nome do lead (perfil do WhatsApp); sem nome, uma saudação neutra. | tudo bem | Ana | 40 |
| {{2}} | `busca` | Resumo da busca a partir da ficha: tipo de imóvel, quartos e região (ex.: "apartamento de 2 quartos em Pinheiros"). | o seu novo imóvel | apartamento de 2 quartos em Pinheiros | 80 |
| {{3}} | `imovel_novo` | Código e título do imóvel novo compatível que o core encontrou no catálogo (ex.: "IMV-012, apartamento de 2 quartos na Vila Mariana"). | um imóvel novo na nossa base | IMV-012, apartamento de 2 quartos na Vila Mariana | 120 |

## `imob_convite_visita`

- **Categoria:** MARKETING
- **Idioma:** pt_BR
- **Usado em:** follow-up qualificado, etapa 1

**Corpo para submissão (numerado):**

```text
Oi, {{1}}! Com o que você me contou sobre a sua busca ({{2}}), o próximo passo é {{3}}. Quer que eu veja um horário para você?
```

**Texto de referência (variáveis nomeadas):**

```text
Oi, {{primeiro_nome}}! Com o que você me contou sobre a sua busca ({{busca}}), o próximo passo é {{proximo_passo}}. Quer que eu veja um horário para você?
```

**Exemplo:** Oi, Ana! Com o que você me contou sobre a sua busca (apartamento de 2 quartos em Pinheiros), o próximo passo é conversar com a nossa especialista em investimentos. Quer que eu veja um horário para você?

| Nº | Variável | Regra de preenchimento | Padrão (fallback) | Exemplo | Limite |
|---|---|---|---|---|---|
| {{1}} | `primeiro_nome` | Primeiro nome do lead (perfil do WhatsApp); sem nome, uma saudação neutra. | tudo bem | Ana | 40 |
| {{2}} | `busca` | Resumo da busca a partir da ficha: tipo de imóvel, quartos e região (ex.: "apartamento de 2 quartos em Pinheiros"). | o seu novo imóvel | apartamento de 2 quartos em Pinheiros | 80 |
| {{3}} | `proximo_passo` | Pela intenção: compra/aluguel → conhecer os imóveis com um corretor; investimento → conversar com a especialista em investimentos. | conhecer os imóveis com um dos nossos corretores | conversar com a nossa especialista em investimentos | 200 |

## `imob_lembrete_agendamento`

- **Categoria:** UTILITY
- **Idioma:** pt_BR
- **Usado em:** lembrete de agendamento

**Corpo para submissão (numerado):**

```text
Oi, {{1}}! Passando para lembrar da sua {{2}} com {{3}}, marcada para {{4}} ({{5}}). Continua de pé? Se precisar, eu remarco para você.
```

**Texto de referência (variáveis nomeadas):**

```text
Oi, {{primeiro_nome}}! Passando para lembrar da sua {{compromisso}} com {{com_quem}}, marcada para {{quando}} ({{onde}}). Continua de pé? Se precisar, eu remarco para você.
```

**Exemplo:** Oi, Ana! Passando para lembrar da sua visita aos imóveis com Rafael Souza, marcada para quinta, 15/10, às 14h (no imóvel). Continua de pé? Se precisar, eu remarco para você.

| Nº | Variável | Regra de preenchimento | Padrão (fallback) | Exemplo | Limite |
|---|---|---|---|---|---|
| {{1}} | `primeiro_nome` | Primeiro nome do lead (perfil do WhatsApp); sem nome, uma saudação neutra. | tudo bem | Ana | 40 |
| {{2}} | `compromisso` | Tipo do agendamento: visita aos imóveis ou reunião sobre investimentos. | visita | visita aos imóveis | 200 |
| {{3}} | `com_quem` | Nome do corretor ou da especialista do agendamento. | a nossa equipe | Rafael Souza | 60 |
| {{4}} | `quando` | Dia da semana, data e hora no fuso da operação (ex.: "quinta, 15/10, às 14h"). | o horário combinado | quinta, 15/10, às 14h | 200 |
| {{5}} | `onde` | Modalidade: no imóvel, online ou no nosso escritório. | como combinamos | no imóvel | 200 |

## `imob_resposta_equipe`

- **Categoria:** UTILITY
- **Idioma:** pt_BR
- **Usado em:** resposta da equipe (tela Fila) fora da janela

**Corpo para submissão (numerado):**

```text
Oi, {{1}}! Aqui é {{2}}, da imobiliária. Tenho um retorno sobre o seu atendimento: pode me responder por aqui para continuarmos?
```

**Texto de referência (variáveis nomeadas):**

```text
Oi, {{primeiro_nome}}! Aqui é {{responsavel}}, da imobiliária. Tenho um retorno sobre o seu atendimento: pode me responder por aqui para continuarmos?
```

**Exemplo:** Oi, Ana! Aqui é Rafael Souza, da imobiliária. Tenho um retorno sobre o seu atendimento: pode me responder por aqui para continuarmos?

| Nº | Variável | Regra de preenchimento | Padrão (fallback) | Exemplo | Limite |
|---|---|---|---|---|---|
| {{1}} | `primeiro_nome` | Primeiro nome do lead (perfil do WhatsApp); sem nome, uma saudação neutra. | tudo bem | Ana | 40 |
| {{2}} | `responsavel` | Nome de quem da equipe está atendendo (tela Fila). | a equipe de atendimento | Rafael Souza | 60 |

## Mapeamento na operação (`.env`)

Em uma linha só no `.env` (aqui formatado para leitura):

```json
{
  "imob_retomada_descoberta": {
    "content_sid": "HX...",
    "idioma": "pt_BR",
    "variaveis": [
      "primeiro_nome"
    ]
  },
  "imob_retomada_regiao": {
    "content_sid": "HX...",
    "idioma": "pt_BR",
    "variaveis": [
      "primeiro_nome",
      "gancho"
    ]
  },
  "imob_encerramento_busca": {
    "content_sid": "HX...",
    "idioma": "pt_BR",
    "variaveis": [
      "primeiro_nome"
    ]
  },
  "imob_retomada_busca": {
    "content_sid": "HX...",
    "idioma": "pt_BR",
    "variaveis": [
      "primeiro_nome",
      "busca",
      "gancho"
    ]
  },
  "imob_novidade_imovel": {
    "content_sid": "HX...",
    "idioma": "pt_BR",
    "variaveis": [
      "primeiro_nome",
      "busca",
      "imovel_novo"
    ]
  },
  "imob_convite_visita": {
    "content_sid": "HX...",
    "idioma": "pt_BR",
    "variaveis": [
      "primeiro_nome",
      "busca",
      "proximo_passo"
    ]
  },
  "imob_lembrete_agendamento": {
    "content_sid": "HX...",
    "idioma": "pt_BR",
    "variaveis": [
      "primeiro_nome",
      "compromisso",
      "com_quem",
      "quando",
      "onde"
    ]
  },
  "imob_resposta_equipe": {
    "content_sid": "HX...",
    "idioma": "pt_BR",
    "variaveis": [
      "primeiro_nome",
      "responsavel"
    ]
  }
}
```
