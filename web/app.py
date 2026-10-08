"""Front Streamlit: chat com a Lia + painel de qualificação ao lado.

Consome SOMENTE a API HTTP — nunca o banco. Chat e painel são fragments com polling
(`run_every`): só essas áreas se redesenham, e o campo de mensagem fica livre.
"""

import os
import uuid
from datetime import datetime
from typing import Any

import httpx
import streamlit as st

API_URL = os.environ.get("API_URL", "http://localhost:8000")
TIMEOUT = httpx.Timeout(10.0, connect=5.0)
POLLING_SEGUNDOS = 1.5
EVENTOS_NO_PAINEL = 20

# Vocabulário de exibição da vertical imobiliária (o backend devolve os nomes técnicos).
INTENCOES = {
    "compra": "🔑 Compra",
    "aluguel": "🏢 Aluguel",
    "investimento": "📈 Investimento",
}
CLASSIFICACOES = {"quente": ("🔥", "red"), "morno": ("🌤️", "orange"), "frio": ("🧊", "blue")}
ACOES = {
    "agendar_visita": "📅 Agendar visita com um corretor",
    "encaminhar_especialista": "🤝 Encaminhar ao especialista em investimentos",
}
# Ordem de exibição = ordem em que a Lia pergunta; campos desconhecidos vão ao fim.
CAMPOS = {
    "regiao": "Região",
    "preco_max": "Preço máximo",
    "aluguel_max": "Aluguel máximo",
    "ticket": "Valor a investir",
    "objetivo": "Objetivo",
    "quartos": "Quartos",
    "tipo_imovel": "Tipo de imóvel",
    "expectativa_retorno": "Retorno esperado",
    "urgencia": "Urgência",
    "prazo_mudanca": "Prazo de mudança",
    "prazo_investimento": "Horizonte",
    "forma_pagamento": "Pagamento",
    "tipo_garantia": "Garantia",
    "experiencia_previa": "Experiência",
    "vagas": "Vagas",
    "aceita_pets": "Pets",
}
CAMPOS_EM_REAIS = {"preco_max", "aluguel_max", "ticket"}
DIAS = ("seg", "ter", "qua", "qui", "sex", "sáb", "dom")
TIPOS_AGENDAMENTO = {
    "visita_imovel": "🏠 Visita aos imóveis",
    "reuniao_especialista": "📈 Reunião com especialista",
}
MODALIDADES = {"presencial": "presencial", "online": "online", "escritorio": "no escritório"}
NAO_INFORMADO = "não informado"
ATENDIMENTO = {
    "atendimento_ia": "🤖 Lia (assistente virtual)",
    "confirmando_handoff": "❓ Lia confirmando a transferência para a equipe",
    "aguardando_humano": "⏳ Na fila, aguardando a equipe",
    "atendimento_humano": "🧑‍💼 Em atendimento humano",
    "confirmando_retorno_ia": "❓ Lia confirmando a volta para a assistente",
}
AVATARES = {"lead": "🙂", "assistente": "🏠", "responsavel": "🧑‍💼"}


def md(texto: object) -> str:
    """Escapa `$` (o markdown do Streamlit trata `$...$` como LaTeX: "R$ 800.000")."""
    return str(texto).replace("$", r"\$")


def formatar_valor(campo: str, valor: object) -> str:
    if isinstance(valor, bool):
        return "sim" if valor else "não"
    if campo in CAMPOS_EM_REAIS and isinstance(valor, int | float):
        return f"R$ {valor:,.0f}".replace(",", ".")
    if campo == "expectativa_retorno" and isinstance(valor, int | float):
        return f"{valor:g}% a.a."
    if isinstance(valor, list):
        return ", ".join(formatar_valor(campo, v) for v in valor)
    return str(valor).replace("_", " ")


def ordenar_campos(campos: list[str]) -> list[str]:
    ordem = list(CAMPOS)
    return sorted(campos, key=lambda c: (ordem.index(c) if c in ordem else len(ordem), c))


def descrever_evento(evento: dict[str, Any]) -> str:
    p = evento["payload"]
    campo = str(p.get("campo"))
    match evento["tipo"]:
        case "IntencaoIdentificada":
            texto = f"intenção identificada: **{p.get('intencao')}**"
        case "IntencaoAlterada":
            texto = f"intenção alterada: **{p.get('de')} → {p.get('para')}**"
        case "CampoQualificacaoPreenchido":
            origem = f" _(herdado de {p['origem'].split(':', 1)[-1]})_" if p.get("origem") else ""
            texto = f"{campo} = {formatar_valor(campo, p.get('valor'))}{origem}"
        case "CampoQualificacaoCorrigido":
            de, para = formatar_valor(campo, p.get("de")), formatar_valor(campo, p.get("para"))
            texto = f"{campo} corrigido: {de} → {para}"
        case "CampoQualificacaoRemovido":
            texto = f"{campo} removido"
        case "ScoreAlterado":
            de = f"{p['de']} → " if p.get("de") is not None else ""
            pontos = f"{de}**{p.get('para')}**"
            texto = f"score {pontos} ({p.get('classificacao_para')})"
        case "LeadQualificado":
            texto = f"**lead qualificado** → {p.get('proxima_acao')}"
        case "LeadCriado":
            texto = "lead criado"
        case outro:
            texto = descrever_evento_pos_qualificacao(outro, p)
    return texto


def _agendamento_criado(p: dict[str, Any]) -> str:
    quem = f"{p.get('responsavel_nome')} ({p.get('responsavel_titulo')})"
    modalidade = MODALIDADES.get(str(p.get("modalidade")), p.get("modalidade"))
    return f"📅 **agendado**: {formatar_horario(p.get('inicio'))} com {quem} · {modalidade}"


EVENTOS_POS_QUALIFICACAO: dict[str, Any] = {
    "AgendamentoCriado": _agendamento_criado,
    "AgendamentoRemarcado": lambda p: (
        f"📅 **remarcado**: {formatar_horario(p.get('inicio_anterior'))} → "
        f"{formatar_horario(p.get('inicio'))}"
    ),
    "AgendamentoCancelado": lambda p: f"📅 **cancelado**: {formatar_horario(p.get('inicio'))}",
    "ResumoHandoffGerado": lambda p: (
        f"📝 resumo v{p.get('versao')} gerado e enviado ao CRM ({p.get('crm_id')})"
    ),
    "HandoffSolicitado": lambda p: f"🙋 pediu atendimento humano ({p.get('motivo')})",
    "HandoffConfirmado": lambda p: "⏳ **entrou na fila** do atendimento humano",
    "HandoffRecusado": lambda p: f"🤖 seguiu com a Lia ({p.get('motivo')})",
    "AtendimentoHumanoIniciado": lambda p: (
        f"🧑‍💼 **{p.get('responsavel')} assumiu** (espera {p.get('espera_segundos')}s)"
    ),
    "AtendimentoHumanoEncerrado": lambda p: f"🤖 {p.get('responsavel')} devolveu para a Lia",
    "RetornoIASolicitado": lambda p: "❓ quer voltar para a Lia",
    "RetornoIAConfirmado": lambda p: "🤖 **saiu da fila** e voltou para a Lia",
    "MensagemDuranteEspera": lambda p: "💬 escreveu enquanto aguardava na fila",
    "HandoffSLAExcedido": lambda p: (
        f"🚨 **SLA da fila estourado** ({p.get('espera_util_minutos')} min úteis)"
    ),
    "FollowUpAgendado": lambda p: (
        f"⏰ follow-up {p.get('etapa')} agendado para {formatar_horario(p.get('executar_em'))}"
    ),
    "FollowUpEnviado": lambda p: (
        f"📨 **follow-up enviado** ({p.get('tipo')}"
        + (f", etapa {p.get('etapa')}" if p.get("etapa") else "")
        + (", requer template" if p.get("requer_template") else "")
        + ")"
    ),
    "LeadEncerradoPorInatividade": lambda p: "💤 cadência encerrada por inatividade",
    "LeadReengajado": lambda p: f"🔁 **reengajou** (respondeu ao follow-up {p.get('etapa')})",
    "LeadOptOut": lambda p: "🛑 **opt-out**: pediu para não receber mais mensagens",
}


def descrever_evento_pos_qualificacao(tipo: str, p: dict[str, Any]) -> str:
    descrever = EVENTOS_POS_QUALIFICACAO.get(tipo)
    return str(descrever(p)) if descrever else tipo


def formatar_horario(iso: object) -> str:
    """ISO 8601 → "qui 08/10 14h" no fuso local de quem vê."""
    try:
        momento = datetime.fromisoformat(str(iso)).astimezone()
    except ValueError:
        return str(iso)
    return f"{DIAS[momento.weekday()]} {momento:%d/%m %Hh%M}".removesuffix("00")


def descrever_agendamento(ag: dict[str, Any]) -> str:
    tipo = TIPOS_AGENDAMENTO.get(ag["tipo"], ag["tipo"])
    r = ag["responsavel"]
    modalidade = MODALIDADES.get(ag["modalidade"], ag["modalidade"])
    return (
        f"**{tipo}** · {formatar_horario(ag['inicio'])} com **{r['nome']}** "
        f"({r['titulo']}) · {modalidade}"
    )


def mostrar_secao(secao: dict[str, Any]) -> None:
    """Uma seção do resumo de handoff, conforme o tipo (o template vem da vertical)."""
    st.markdown(f"**{secao['titulo']}**")
    conteudo = secao["conteudo"]
    if conteudo == NAO_INFORMADO:
        st.caption(NAO_INFORMADO)
        return
    match secao["tipo"]:
        case "lista":
            linhas = [f"- {i['texto']} — _“{i['evidencia']}”_" for i in conteudo]
        case "itens_catalogo":
            linhas = [f"- **{i['id']}** {i['titulo']} — {i['reacao']}" for i in conteudo]
        case "trechos":
            linhas = [f"> “{trecho}”" for trecho in conteudo]
        case "ficha":
            linhas = [
                f"- {CAMPOS.get(c, c)}: "
                + (f"_{NAO_INFORMADO}_" if v == NAO_INFORMADO else formatar_valor(c, v))
                for c, v in conteudo.items()
            ]
        case "score":
            motivos = "".join(f"\n  - {m}" for m in conteudo["motivos"])
            linhas = [f"- {conteudo['pontos']}/100 · {conteudo['classificacao']}{motivos}"]
        case "agendamento":
            situacao = "" if conteudo["status"] == "ativo" else f" ({conteudo['status']})"
            linhas = [
                f"- {TIPOS_AGENDAMENTO.get(conteudo['tipo'], conteudo['tipo'])}: "
                f"{formatar_horario(conteudo['inicio'])} com {conteudo['responsavel']} · "
                f"{MODALIDADES.get(conteudo['modalidade'], conteudo['modalidade'])}{situacao}"
            ]
        case _:
            linhas = [str(conteudo)]
    st.markdown(md("\n".join(linhas)))


def mostrar_atendimento(atendimento: dict[str, Any]) -> None:
    situacao = ATENDIMENTO.get(atendimento["estado"], atendimento["estado"])
    if atendimento["responsavel"]:
        situacao += f" com **{atendimento['responsavel']}**"
    if atendimento["na_fila_desde"] and atendimento["estado"] != "atendimento_humano":
        situacao += f" · desde {formatar_horario(atendimento['na_fila_desde'])}"
    st.markdown(md(f"**Atendimento:** {situacao}"))


def mostrar_followup(lead: dict[str, Any]) -> None:
    """Próximo follow-up + botão de demo que "faz o lead sumir" (o worker envia já)."""
    if lead.get("opt_out_em"):
        st.caption("🛑 Opt-out: sem mensagens ativas para este lead")
        return
    proximos = [f for f in lead.get("followups", []) if f["tipo"] == "retomada"]
    texto = (
        f"⏰ Próximo follow-up: etapa {proximos[0]['etapa']} em "
        f"{formatar_horario(proximos[0]['executar_em'])}"
        if proximos
        else "⏰ Sem follow-up programado"
    )
    colunas = st.columns([3, 2])
    colunas[0].caption(texto)
    if colunas[1].button("⏩ Simular inatividade", key="simular_inatividade") and (
        falha := api_post(f"/demo/leads/{lead['lead_id']}/simular-inatividade")
    ):
        st.error(falha)


def mostrar_score(lead: dict[str, Any]) -> None:
    if lead["score"] is None:
        st.markdown("**Score:** _aguardando a intenção_")
        return
    emoji, cor = CLASSIFICACOES.get(lead["classificacao"], ("•", "gray"))
    st.markdown(f"**Score:** :{cor}[**{lead['score']}/100 · {emoji} {lead['classificacao']}**]")
    st.progress(min(max(lead["score"], 0), 100) / 100)
    with st.expander("Por que esse score?", expanded=True):
        st.markdown(md("\n".join(f"- {m}" for m in lead["score_motivos"])) or "—")


def mostrar_resumo(lead_id: str, *, recolhido: bool) -> None:
    """Resumo para quem vai atender (tela Fila). `recolhido`: num expander, para ler antes
    de assumir; senão, num quadro aberto (dentro de outro expander não cabe expander)."""
    try:
        resposta = httpx.get(f"{API_URL}/leads/{lead_id}/resumo", timeout=TIMEOUT)
    except httpx.HTTPError:
        return
    if resposta.status_code == httpx.codes.NOT_FOUND:
        st.caption("📝 Resumo ainda não gerado (sai em instantes após o handoff).")
        return
    if resposta.is_error:
        return
    resumo: dict[str, Any] = resposta.json()
    titulo = (
        f"📝 {resumo['titulo']} · v{resumo['versao']} ({formatar_horario(resumo['gerado_em'])})"
    )
    area = st.expander(titulo) if recolhido else st.container(border=True)
    with area:
        if not recolhido:
            st.markdown(f"**{titulo}**")
        st.caption(f"gerado por {resumo['gatilho']} · também enviado ao CRM")
        for secao in resumo["secoes"]:
            mostrar_secao(secao)
        if resumo["descartados"]:
            st.caption(
                f"⚓ {len(resumo['descartados'])} item(ns) descartado(s) por falta de lastro"
            )


def api_get(caminho: str, **params: Any) -> Any:
    resposta = httpx.get(f"{API_URL}{caminho}", params=params, timeout=TIMEOUT)
    resposta.raise_for_status()
    return resposta.json()


def api_enviar(lead_id: str, texto: str) -> dict[str, Any]:
    resposta = httpx.post(
        f"{API_URL}/conversas/mensagens",
        json={"lead_id": lead_id, "texto": texto},
        timeout=TIMEOUT,
    )
    if resposta.is_error:
        detalhe = resposta.json().get("detail", resposta.text)
        raise RuntimeError(f"{resposta.status_code}: {detalhe}")
    corpo: dict[str, Any] = resposta.json()
    return corpo


def novo_lead_id() -> str:
    return f"lead-{uuid.uuid4().hex[:6]}"


def mostrar_itens(itens: list[dict[str, Any]]) -> None:
    if not itens:
        return
    with st.expander(f"🏠 {len(itens)} imóvel(is) citado(s) — dados da base"):
        for item in itens:
            st.markdown(md(f"**{item['id']}** · {item.get('titulo', '')}"))
            st.caption(md(item.get("resumo", "")))


def mostrar_ficha(ficha: dict[str, Any], faltantes: list[str]) -> None:
    linhas = [
        f"✅ **{CAMPOS.get(c, c)}:** {formatar_valor(c, ficha[c])}"
        for c in ordenar_campos(list(ficha))
    ]
    linhas += [f"⬜ {CAMPOS.get(c, c)}" for c in faltantes if c not in ficha]
    st.markdown(md("  \n".join(linhas)) if linhas else "_ainda vazia_")


st.set_page_config(page_title="Lia — SDR Imobiliário", page_icon="🏠", layout="wide")

# ------------------------------------------------------------------ barra lateral: leads
with st.sidebar:
    st.header("Leads")
    try:
        leads: list[dict[str, Any]] = api_get("/leads")
    except httpx.HTTPError as erro:
        st.error(f"API indisponível em {API_URL}: {erro}")
        st.stop()

    if st.button("➕ Novo lead", use_container_width=True):
        st.session_state.lead_id = novo_lead_id()

    with st.form("abrir_lead", clear_on_submit=True):
        digitado = st.text_input("Abrir/criar pelo lead_id", placeholder="ex.: lead-ana")
        if st.form_submit_button("Abrir") and digitado.strip():
            st.session_state.lead_id = digitado.strip()

    ids = [lead["lead_id"] for lead in leads]
    if "lead_id" not in st.session_state:
        st.session_state.lead_id = ids[0] if ids else novo_lead_id()
    atual: str = st.session_state.lead_id
    opcoes = ids if atual in ids else [atual, *ids]
    rotulos = {
        lead["lead_id"]: f"{lead['lead_id']} ({lead['total_mensagens']} msgs)" for lead in leads
    }
    escolhido = st.radio(
        "Conversas",
        opcoes,
        index=opcoes.index(atual),
        format_func=lambda i: rotulos.get(i, f"{i} (novo)"),
    )
    if escolhido != atual:
        st.session_state.lead_id = escolhido
        st.rerun()
    st.radio(
        "Tela",
        ["chat", "fila"],
        key="tela",
        format_func=lambda t: {"chat": "💬 Chat (lead)", "fila": "🧑‍💼 Fila (equipe)"}[t],
        horizontal=True,
    )
    st.caption(f"API: {API_URL}")


# ------------------------------------------------------------------ conversa + painel
def tela_fila() -> None:
    """Tela da equipe: fila de quem aguarda, assumir, responder e devolver para a Lia."""
    st.title("🧑‍💼 Fila de atendimento humano")
    responsavel = st.text_input("Seu nome (quem está atendendo)", key="responsavel_nome")
    fila_e_atendimentos(responsavel.strip() or "Equipe")


def api_post(caminho: str, corpo: dict[str, Any] | None = None) -> str | None:
    """POST na API; devolve a mensagem de erro (ou None se deu certo)."""
    try:
        resposta = httpx.post(f"{API_URL}{caminho}", json=corpo or {}, timeout=TIMEOUT)
    except httpx.HTTPError as erro:
        return str(erro)
    if resposta.is_error:
        return str(resposta.json().get("detail", resposta.text))
    return None


def minutos(segundos: int) -> str:
    return f"{segundos // 60} min {segundos % 60:02d} s"


@st.fragment(run_every=3)
def fila_e_atendimentos(responsavel: str) -> None:
    try:
        aguardando: list[dict[str, Any]] = api_get("/atendimentos/fila")
        em_atendimento: list[dict[str, Any]] = api_get("/atendimentos")
    except httpx.HTTPError as erro:
        st.warning(f"Sem conexão com a API: {erro}")
        return

    st.subheader(f"⏳ Aguardando ({len(aguardando)})")
    if not aguardando:
        st.caption("Ninguém na fila.")
    for item in aguardando:
        lead = item["lead_id"]
        colunas = st.columns([3, 2, 2, 2])
        colunas[0].markdown(f"**{lead}**")
        colunas[1].markdown(f"esperando há {minutos(item['espera_segundos'])}")
        colunas[2].markdown(f"{item['intencao'] or '—'} · score {item['score'] or '—'}")
        if colunas[3].button("Assumir", key=f"assumir-{lead}") and (
            falha := api_post(f"/atendimentos/{lead}/assumir", {"responsavel": responsavel})
        ):
            st.error(falha)
        mostrar_resumo(lead, recolhido=True)

    st.subheader(f"🧑‍💼 Em atendimento ({len(em_atendimento)})")
    for item in em_atendimento:
        lead = item["lead_id"]
        quem = item["atendimento"]["responsavel"]
        with st.expander(f"{lead} — com {quem}", expanded=True):
            mostrar_resumo(lead, recolhido=False)
            for m in api_get(f"/conversas/{lead}/mensagens")["mensagens"][-12:]:
                autor = {"lead": "🙂 Lead", "assistente": "🏠 Lia"}.get(
                    m["papel"], f"🧑‍💼 {m.get('responsavel') or 'Equipe'}"
                )
                st.markdown(md(f"**{autor}:** {m['texto']}"))
            with st.form(f"responder-{lead}", clear_on_submit=True):
                texto = st.text_area("Responder ao lead", key=f"texto-{lead}")
                if st.form_submit_button("Enviar") and texto.strip():
                    corpo = {"texto": texto, "responsavel": responsavel}
                    if falha := api_post(f"/atendimentos/{lead}/mensagens", corpo):
                        st.error(falha)
            if st.button("↩️ Devolver para a Lia", key=f"devolver-{lead}") and (
                falha := api_post(f"/atendimentos/{lead}/devolver")
            ):
                st.error(falha)


if st.session_state.get("tela") == "fila":
    tela_fila()
    st.stop()

lead_id: str = st.session_state.lead_id
st.title("🏠 Lia — consultora da imobiliária")
st.caption(
    f"Conversando como **{lead_id}** · pode mandar várias mensagens seguidas: a Lia espera "
    "você terminar e responde ao conjunto · reabra este lead_id para continuar"
)

# O envio só registra a mensagem (HTTP 202); a resposta chega pelo polling abaixo.
if texto := st.chat_input("Escreva como se fosse no WhatsApp…"):
    try:
        api_enviar(lead_id, texto)
    except (RuntimeError, httpx.HTTPError) as erro:
        st.error(f"Não foi possível enviar: {erro}")


@st.fragment(run_every=POLLING_SEGUNDOS)
def conversa(lead_id: str) -> None:
    """Reexecuta sozinho a cada POLLING_SEGUNDOS — só esta área, o input segue livre."""
    try:
        historico = api_get(f"/conversas/{lead_id}/mensagens")
    except httpx.HTTPError as erro:
        st.warning(f"Sem conexão com a API: {erro}")
        return
    for mensagem in historico["mensagens"]:
        papel = "user" if mensagem["papel"] == "lead" else "assistant"
        with st.chat_message(papel, avatar=AVATARES.get(mensagem["papel"], "🏠")):
            if mensagem["papel"] == "responsavel":
                st.caption(f"{mensagem.get('responsavel') or 'Equipe'} · atendimento humano")
            st.markdown(md(mensagem["texto"]))
            if mensagem["status"] == "falha":
                st.caption("⚠️ a Lia não conseguiu responder a esta mensagem")
            mostrar_itens(mensagem.get("itens_citados", []))
    if historico["processando"]:
        with st.chat_message("assistant", avatar="🏠"):
            st.markdown("_Lia está digitando…_")


@st.fragment(run_every=POLLING_SEGUNDOS)
def painel(lead_id: str) -> None:
    """Qualificação em tempo real (GET /leads/{lead_id}): intenção, ficha, score, ação."""
    st.subheader("📋 Qualificação")
    try:
        resposta = httpx.get(f"{API_URL}/leads/{lead_id}", timeout=TIMEOUT)
    except httpx.HTTPError as erro:
        st.warning(f"Sem conexão com a API: {erro}")
        return
    if resposta.status_code == httpx.codes.NOT_FOUND:
        st.info("Envie a primeira mensagem para começar a qualificação.")
        return
    if resposta.is_error:
        st.warning(f"Erro ao consultar o lead: {resposta.status_code}")
        return
    lead: dict[str, Any] = resposta.json()

    intencao = lead["intencao"]
    rotulo = INTENCOES.get(intencao, intencao) if intencao else "❔ indefinida"
    mostrar_atendimento(lead["atendimento"])
    st.markdown(f"**Intenção:** {rotulo}")

    mostrar_score(lead)

    if acao := lead["proxima_acao"]:
        st.success(f"**Próxima ação:** {ACOES.get(acao, acao)}")
    elif intencao:
        st.caption("Próxima ação: definida quando os dados essenciais estiverem na ficha")
    if agendamento := lead.get("agendamento"):
        st.info(md(f"📅 {descrever_agendamento(agendamento)}"))
    mostrar_followup(lead)

    if intencao:
        st.markdown(f"**Ficha de {intencao}**")
        mostrar_ficha(lead["ficha"], lead["campos_faltantes"])
    for outra, ficha in lead["fichas"].items():
        if outra != intencao and ficha:
            with st.expander(f"Ficha de {outra} (preservada)"):
                mostrar_ficha(ficha, [])

    eventos = lead["eventos"]
    with st.expander(f"Trilha de eventos ({len(eventos)})"):
        for evento in reversed(eventos[-EVENTOS_NO_PAINEL:]):
            hora = datetime.fromisoformat(evento["ocorrido_em"]).astimezone().strftime("%H:%M:%S")
            st.markdown(md(f"`{hora}` {descrever_evento(evento)}"))


col_chat, col_painel = st.columns([3, 2], gap="large")
with col_chat:
    conversa(lead_id)
with col_painel:
    painel(lead_id)
