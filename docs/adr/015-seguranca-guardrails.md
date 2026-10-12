# ADR 015 — Segurança e guardrails: defesa em camadas, controles na fronteira certa

- **Status:** Aceita
- **Data:** 2026-10-12
- **Relacionados:** [ADR 004](004-agente-llm-memoria.md), [ADR 006](006-processamento-assincrono-debounce.md),
  [ADR 010](010-limites-do-assistente.md), [ADR 012](012-canal-whatsapp-templates.md),
  [ADR 013](013-observabilidade-langfuse.md) · detalhes em [docs/seguranca.md](../seguranca.md)

## Contexto

A POC vai para a nuvem com URL pública: o chat e o webhook recebem texto de qualquer
pessoa, o painel mostra dados pessoais de leads e o LLM escreve em nome da empresa.
Precisamos tratar prompt injection, assunto fora do domínio, promessas comerciais
indevidas, abuso/custo, acesso não autenticado, vazamento de PII e os direitos do titular
(LGPD), sem quebrar core × vertical nem o fluxo assíncrono.

## Decisão

1. **Defesa estrutural primeiro.** O agente só tem as ferramentas do catálogo e só recebe
   o contexto do próprio lead: não existe caminho para "listar leads". Detecção e prompts
   são camadas a mais, não a defesa principal.
2. **Hierarquia de instruções** (`seguranca_v1`, em toda resposta): só as mensagens de
   sistema instruem; fala do lead, histórico e resultados de ferramentas (catálogo) são
   DADOS. Os classificadores (roteador, extração, atendimento, agenda) levam
   `guarda_dados_v1`.
3. **Detecção de injeção em duas vias**, no início do grafo: heurística pura
   (`core/domain/seguranca.py`, padrões genéricos pt/en) OU `tentativa_injecao` do
   roteador (`roteador_v4`). Detectada ⇒ nó `redirecionar`: sem ferramentas, sem extração,
   sem troca de intenção, sem handoff/opt-out/agenda disparados pela fala; a persona
   recusa no papel e retoma o atendimento. Evento `TentativaInjecaoDetectada` (origens e
   padrões, nunca a fala). Mesmo padrão já validado no projeto: sinal estruturado muda o
   BLOCO do turno (prompt sozinho perde para o roteiro).
4. **Fora do domínio:** `fora_do_dominio` do roteador ⇒ mesmo nó, recusa gentil em uma
   frase e volta ao próximo dado/ação. Não é evento (não é ataque).
5. **Validação de saída no turno** (`ProcessarTurno`): códigos citados existem no catálogo
   (já existia) E nenhuma promessa indevida (`PADROES_PROMESSA` do core + `Persona.
   padroes_promessa` da vertical). Reprovada ⇒ regenera UMA vez com a correção ⇒ resposta
   segura da persona. Evento `RespostaReprovadaNaValidacao`.
6. **Limites:** `MENSAGEM_MAX_CARACTERES` no `ReceberMensagem` (core, todo canal; web 422,
   WhatsApp trunca e marca). Rate limit por IP e por remetente atrás do
   `LimitadorTaxaPort` — adapter em memória (1 réplica, ADR 006); com várias réplicas,
   troca-se por Redis/Postgres sem mudar quem chama. Webhook acima do limite responde 200
   e descarta (4xx/5xx faria o provedor reenviar).
7. **Autenticação:** rotas administrativas e de atendimento exigem `X-API-Key`
   (`ADMIN_API_KEYS`, comparação em tempo constante). Públicos: `/health`, chat web (só
   leads do canal web, sem metadados internos), catálogo da vertical (rate limit) e
   webhooks (assinatura do provedor). `AMBIENTE=producao`: sem chave a API não sobe;
   `/demo/*`, `/docs` e o OpenAPI não são registrados. Dashboard com login próprio
   (usuário + hash PBKDF2 via env) e fala com a API pela chave de admin.
8. **PII:** `mascarar_pii` (domínio) continua o ponto único — logs (agora também
   traceback), Langfuse e o log de exportação do CRM mock. Minimização: CPF não vai ao
   LLM.
9. **LGPD:** `ExportarDadosLead` e `AnonimizarLead` (core/application) sobre
   `DadosTitularPort` (`DadosTitularSql`, atômico). Anonimizar cancela agenda e follow-ups,
   troca o remetente por `anon-<id>`, apaga nome, fichas, textos, resumos e CRM e mantém
   o que alimenta métricas. Evento `DadosLeadAnonimizados`.

## Consequências

- O core ganha vocabulário genérico de segurança (sem segmento); a vertical só acrescenta
  promessas típicas e a resposta segura da persona.
- Detecção por regex e LLM tem falso negativo (jailbreak novo) e falso positivo (fala
  legítima redirecionada uma vez): os riscos residuais estão em `docs/seguranca.md`, e o
  eval (Dia 5C) mede as duas taxas.
- Rate limit e login do Streamlit valem por processo/sessão: com escala horizontal ou
  ataque distribuído, precisam de armazenamento compartilhado e de um IdP (roadmap).
