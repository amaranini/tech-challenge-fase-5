# ADR 002 — Busca híbrida no Postgres com embeddings locais

- **Status:** Aceita
- **Data:** 2026-10-06

## Contexto

A Lia só pode sugerir imóveis que existem na base. O lead pede coisas como
"apê 2 quartos zona sul até 800 mil perto do metrô", que misturam:

- **restrições duras** (preço, quartos, zona, finalidade, distância do metrô), em que um erro
  é inaceitável: não se oferece um imóvel de R$ 950 mil a quem disse "até 800";
- **preferências subjetivas** ("tranquilo", "bom para família", "perto de parque"), que só
  uma busca semântica captura.

Embeddings sozinhos não respeitam números, e filtros sozinhos não entendem "aconchegante".

## Decisão

1. **Busca híbrida em uma única query SQL**: um `WHERE` com os filtros estruturados e um
   `ORDER BY embedding <=> :consulta` (distância de cosseno do pgvector). O Postgres já é a
   base transacional, então não há segundo sistema para sincronizar.
2. **Índice HNSW** (`vector_cosine_ops`) com `hnsw.iterative_scan = relaxed_order`
   (pgvector ≥ 0.8). Assim os filtros no `WHERE` não reduzem o número de resultados quando
   o índice entrar em uso, com volumes maiores.
3. **Embeddings locais com fastembed (ONNX)** e o modelo
   `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` (384 dimensões, cerca de
   220 MB):
   - é multilíngue e funciona bem com português;
   - não precisa de API externa (custo zero, sem enviar dados de imóveis/leads a terceiros);
   - não depende de torch, o que deixa a imagem bem menor que com sentence-transformers;
   - o modelo é embutido na imagem Docker no build, e o runtime roda offline.
4. **O texto indexado** é `Imovel.texto_semantico()`: título, tipo, bairro, zona, descrição,
   estação de metrô e comodidades. Fica além da descrição pura para que bairro e lazer
   também pesem na similaridade.
5. **Os ports foram separados** para permitir trocar cada peça:
   - `EmbeddingPort`, com `gerar_documentos` e `gerar_consulta` separados, prepara para
     modelos assimétricos (e5, bge);
   - `ImovelRepository` (CRUD) fica separado de `BuscaImoveisPort` (`indexar` + `buscar`).
     `indexar` recebe o imóvel inteiro, para que um índice externo (Qdrant, OpenSearch)
     possa guardar os campos filtráveis como payload;
   - `InterpretadorConsultaPort` converte texto livre em `CriteriosBusca`.
6. **O interpretador começa por regras (regex)**: é determinístico, testável, não tem
   latência nem custo e cobre o vocabulário comum (apê, dorms, "até 800 mil", ZS, metrô,
   pet, mobiliado). Os filtros explícitos da requisição prevalecem sobre os inferidos. No
   agente (Etapa C), o próprio LLM preenche os filtros como argumentos da tool, e o
   interpretador fica como apoio para a API e as buscas diretas.

## Consequências

- **Trocar o modelo para outra dimensão exige uma migration nova** na coluna `vector(N)` e
  a reindexação com `scripts/seed_imoveis.py`. O bootstrap falha no start se
  `EMBEDDING_DIMENSAO` divergir da coluna.
- **A similaridade do MiniLM é modesta**, na faixa de 0,3 a 0,6, e serve para ordenar, não
  como limiar absoluto. Se a qualidade não bastar no eval (Dia 5), há três caminhos, sem
  tocar no núcleo:
  - `multilingual-e5-large`, só trocando o adapter e fazendo a migration;
  - um reranker;
  - um interpretador via LLM.
- **"Perto do metrô" vale 1.000 m**, configurável em `BUSCA_DISTANCIA_METRO_PADRAO_M`.
- **O que o regex não reconhece**, como bairro citado no texto, entra só pela via semântica.

## Alternativas consideradas

- **Vector DB dedicado (Qdrant, Pinecone):** é mais um serviço para operar e sincronizar,
  e não se justifica para centenas ou milhares de imóveis. O port permite migrar depois.
- **Embeddings via API (Voyage, OpenAI):** têm qualidade melhor, mas trazem custo, latência,
  dependência externa e envio de dados a terceiros. Podem virar outro adapter de
  `EmbeddingPort`.
- **sentence-transformers:** dá o mesmo modelo, mas exige torch (mais de 2 GB na imagem).
- **Full-text search (tsvector) + BM25 com fusão RRF:** fica como evolução possível se o
  eval mostrar falhas em termos exatos.
