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
            texto = str(outro)
    return texto


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
    st.caption(f"API: {API_URL}")

# ------------------------------------------------------------------ conversa + painel
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
        with st.chat_message(papel, avatar="🙂" if papel == "user" else "🏠"):
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
    st.markdown(f"**Intenção:** {rotulo}")

    if lead["score"] is None:
        st.markdown("**Score:** _aguardando a intenção_")
    else:
        emoji, cor = CLASSIFICACOES.get(lead["classificacao"], ("•", "gray"))
        st.markdown(f"**Score:** :{cor}[**{lead['score']}/100 · {emoji} {lead['classificacao']}**]")
        st.progress(min(max(lead["score"], 0), 100) / 100)
        with st.expander("Por que esse score?", expanded=True):
            st.markdown(md("\n".join(f"- {m}" for m in lead["score_motivos"])) or "—")

    if acao := lead["proxima_acao"]:
        st.success(f"**Próxima ação:** {ACOES.get(acao, acao)}")
    elif intencao:
        st.caption("Próxima ação: definida quando os dados essenciais estiverem na ficha")

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
