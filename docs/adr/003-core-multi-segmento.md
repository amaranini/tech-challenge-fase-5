# ADR 003 — Core de SDR genérico e verticais de negócio

- **Status:** Aceita
- **Data:** 2026-10-06
- **Relacionados:** [ADR 001](001-arquitetura-hexagonal.md) (hexagonal) e
  [ADR 002](002-busca-hibrida-embeddings-locais.md) (busca híbrida)

> O pedido original sugeria o nome `002-core-multi-segmento.md`. Como o 002 já registra a
> busca híbrida, esta decisão ficou com o número 003.

## Contexto

Ao fim da Etapa B, o hexágono isolava bem frameworks e infraestrutura, mas o núcleo misturava
dois tipos de conhecimento:

- **o que é SDR conversacional**: lead, conversa, mensagem, qualificação, score, agendamento,
  follow-up, canal;
- **o que é imobiliário**: imóvel, zona, metrô, corretor, compra/aluguel/investimento.

O mesmo agente serve a outros segmentos (concessionárias, escolas, clínicas, SaaS B2B), que
trocam o catálogo, as intenções, a ficha de qualificação, o scoring e a persona, mas mantêm o
fluxo. Se nada fosse feito agora, a Etapa C criaria Lead, Conversa e o agente já acoplados a
imóvel, e separar depois custaria uma reescrita.

## Decisão

1. **Duas árvores:**
   - `src/sdr/core/` é o SDR genérico, um hexágono completo com domain, application e
     adapters de infraestrutura genérica (HTTP, banco, embeddings e, depois, LLM e agente);
   - `src/sdr/verticals/<segmento>/` é uma vertical, com fatias hexagonais (`catalogo/` hoje;
     `persona/` e `qualificacao/` depois).
2. **Regra de dependência entre elas:** o core não importa verticals, e verticals só dependem
   do core. Isso é garantido pelo import-linter e por um teste que procura vocabulário de
   imóveis em `core/`.
3. **O contrato é `VerticalPack`** (`core/vertical.py`), um Protocol com `nome` e
   `montar(InfraCompartilhada) -> VerticalMontada`:
   - `InfraCompartilhada` é o que o core oferece à vertical: sessões do banco e embeddings;
   - `VerticalMontada` é o que a vertical devolve: `catalogo` (CatalogoPort), `routers` e
     `carregar_catalogo_inicial`;
   - `montar` funciona como o composition root da vertical, que instancia os próprios
     adapters sobre a infra do core.
4. **`CatalogoPort` é genérico**: recebe `ConsultaCatalogo(texto, filtros: Mapping, limite)` e
   devolve `ResultadoCatalogo(ItemCatalogo, relevancia)`, além de `obter(ids)`, que prova que um
   item citado existe. Os filtros chegam como dicionário no vocabulário da vertical, que os
   valida. Na vertical imobiliária, o Pydantic rejeita chaves desconhecidas, para que o agente
   não invente filtros.
5. **A busca tipada da vertical foi preservada.** O antigo `BuscaImoveisPort` virou
   `IndiceImoveisPort`, interno da vertical. O `CatalogoImobiliario` adapta o dicionário para
   `CriteriosBusca`, chama `BuscarImoveis` e converte `Imovel` em `ItemCatalogo`. A rota
   `/imoveis/busca` continua usando `BuscarImoveis` direto e é registrada pelo pack, com a mesma
   URL e o mesmo contrato.
6. **A vertical ativa é escolhida por `VERTICAL=imobiliario`**, no registro `VERTICAIS` do
   bootstrap. A vertical tem config própria (`SettingsImobiliario`, prefixo `IMOBILIARIO_`),
   invisível para o core.
7. **A ficha de qualificação será JSONB** no core, tratada como dado opaco, e validada pelo
   schema Pydantic da vertical através de um Protocol do core. Vale da Etapa C em diante.
8. **Persistência:** um único `Base` ORM no core e um único histórico de migrations.
   Migrations já aplicadas não são reescritas; o refactor não alterou o schema, como confirma
   `alembic check`.
9. **YAGNI:** não há segunda vertical nem motor de configuração genérico. O `VerticalPack` só
   ganha um campo quando ele é usado: persona, prompts e tools na Etapa C; intenções,
   validador da ficha, scoring e especialistas no Dia 2; cadência de follow-up no Dia 3.

## Consequências

**Positivas**

- Uma nova vertical é um novo pacote em `verticals/` mais uma linha em `VERTICAIS`, sem tocar
  no core.
- Na Etapa C, Lead, Conversa, Mensagem, o agente e a tool de busca nascem genéricos: a tool
  chama `CatalogoPort`, e a persona Lia e os prompts ficam na vertical.
- A fronteira é verificável no CI: são 8 contratos de import e o teste de vocabulário.

**Negativas / custos**

- **Mais indireção.** Os filtros cruzam a fronteira como dicionário, o que troca checagem
  estática por validação em tempo de execução, feita pela vertical.
- **Dois composition roots** (`bootstrap.py` e `pack.py`). A regra "só aqui se instanciam
  adapters" passa a valer para os dois.
- **A dimensão do embedding fica acoplada à coluna da vertical.** O pack recusa montar se as
  duas divergirem.
- **Nova fatia na vertical exige atualizar os contratos** do import-linter.

## Alternativas consideradas

- **Manter tudo no núcleo e separar quando surgir a segunda vertical:** é mais barato hoje,
  mas a Etapa C e o Dia 2 espalhariam "imóvel" por Lead, ficha, scoring e prompts, e o custo
  da separação cresceria a cada dia.
- **Motor de configuração (verticais em YAML/JSON, sem código):** é flexível, mas especula
  sobre requisitos que não temos, e regras como scoring e interpretação de consulta são
  código de verdade.
- **Plugins por entry points:** o carregamento dinâmico não se justifica com uma vertical. O
  registro explícito no bootstrap basta e é tipado.
