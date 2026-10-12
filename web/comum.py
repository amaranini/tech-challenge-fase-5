"""Peças comuns das páginas do Streamlit: acesso à API, vocabulário de exibição da
vertical, formatação, componentes (resumo, ficha, score, envio) e gráficos.

Consome SOMENTE a API HTTP — nunca o banco.
"""

import os
import uuid
from datetime import date, datetime, time, timedelta
from typing import Any

import altair as alt
import httpx
import streamlit as st

API_URL = os.environ.get("API_URL", "http://localhost:8000")
# Chave de admin da API: as telas da equipe usam rotas protegidas. API_KEY ou, se vazio,
# a primeira de ADMIN_API_KEYS (o mesmo .env da API no docker compose).
_CHAVE = os.environ.get("API_KEY") or os.environ.get("ADMIN_API_KEYS", "").split(",")[0].strip()
HEADERS = {"X-API-Key": _CHAVE} if _CHAVE else {}
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
CANAIS = {"web": "💻", "whatsapp": "📱"}
ENTREGA = {
    "enviada": "✓ enviada",
    "entregue": "✓✓ entregue",
    "lida": "✓✓ lida",
    "falhou": "⚠️ falhou",
}


def rotulo_lead(lead: dict[str, Any]) -> str:
    """ "📱 Ana (whatsapp:+5511…)" — canal, nome do perfil (se houver) e id."""
    icone = CANAIS.get(lead.get("canal", "web"), "")
    nome = f"{lead['nome']} · " if lead.get("nome") else ""
    return f"{icone} {nome}{lead['lead_id']}"


def mostrar_envio(mensagem: dict[str, Any]) -> None:
    """Como a mensagem saiu: texto livre, template (fora da janela) ou bloqueada; e o
    status de entrega informado pelo provedor (WhatsApp)."""
    envio = mensagem.get("envio") or {}
    notas = []
    if envio.get("modo") == "template":
        notas.append(f"📄 template `{envio.get('template')}` (fora da janela de 24h)")
    elif envio.get("modo") == "bloqueado" or mensagem["status"] == "nao_enviada":
        st.warning("Não enviada: fora da janela de 24h e sem template aprovado para ela.")
    if entrega := mensagem.get("entrega"):
        notas.append(ENTREGA.get(entrega, entrega))
    if url := mensagem.get("trace_url"):
        notas.append(f"[🔎 trace]({url})")
    if notas:
        st.caption(" · ".join(notas))


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
        + (", por template" if p.get("requer_template") else "")
        + (", falhou" if p.get("entrega") == "falhou" else "")
        + ")"
    ),
    "LeadEncerradoPorInatividade": lambda p: "💤 cadência encerrada por inatividade",
    "LeadReengajado": lambda p: f"🔁 **reengajou** (respondeu ao follow-up {p.get('etapa')})",
    "LeadOptOut": lambda p: "🛑 **opt-out**: pediu para não receber mais mensagens",
    "EnvioTemplateIndisponivel": lambda p: (
        f"📄 **template indisponível**, mensagem não enviada ({p.get('motivo')})"
    ),
    "MensagemNaoEntregue": lambda p: f"⚠️ **mensagem não entregue** ({p.get('motivo')})",
    "TentativaInjecaoDetectada": lambda p: (
        f"🛡️ **tentativa de injeção** contida ({', '.join(p.get('padroes') or []) or 'LLM'})"
    ),
    "RespostaReprovadaNaValidacao": lambda p: (
        "🧯 resposta reprovada na validação"
        + (" — enviada a resposta segura" if p.get("fallback") else " — corrigida")
    ),
    "ResumoAvaliado": lambda p: (
        f"{'👍' if p.get('util') else '👎'} resumo v{p.get('versao')} avaliado"
        + (f" por {p.get('responsavel')}" if p.get("responsavel") else "")
    ),
    "DadosLeadAnonimizados": lambda p: "🔒 **dados anonimizados** (LGPD)",
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
        resposta = httpx.get(f"{API_URL}/leads/{lead_id}/resumo", headers=HEADERS, timeout=TIMEOUT)
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
        _avaliar_resumo(lead_id, int(resumo["versao"]))


def _avaliar_resumo(lead_id: str, versao: int) -> None:
    """ "Útil / não útil" de quem atende (evento ResumoAvaliado → página Qualidade da IA)."""
    chave = f"resumo_avaliado:{lead_id}:{versao}"
    if st.session_state.get(chave):
        st.caption(f"Obrigado pelo retorno ({st.session_state[chave]}).")
        return
    st.caption("Este resumo ajudou?")
    util, nao_util, _ = st.columns([1, 1, 4])
    escolha = None
    if util.button("👍 Útil", key=f"{chave}:sim"):
        escolha = True
    if nao_util.button("👎 Não útil", key=f"{chave}:nao"):
        escolha = False
    if escolha is not None:
        erro = api_post(
            f"/leads/{lead_id}/resumo/avaliacao",
            {
                "util": escolha,
                "versao": versao,
                "responsavel": st.session_state.get("responsavel_nome") or None,
            },
        )
        if erro:
            st.error(f"Não foi possível registrar: {erro}")
        else:
            st.session_state[chave] = "útil" if escolha else "não útil"
            st.rerun()


def api_get(caminho: str, **params: Any) -> Any:
    resposta = httpx.get(f"{API_URL}{caminho}", params=params, headers=HEADERS, timeout=TIMEOUT)
    resposta.raise_for_status()
    return resposta.json()


def api_enviar(lead_id: str, texto: str) -> dict[str, Any]:
    resposta = httpx.post(
        f"{API_URL}/conversas/mensagens",
        json={"lead_id": lead_id, "texto": texto},
        headers=HEADERS,
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


def api_post(caminho: str, corpo: dict[str, Any] | None = None) -> str | None:
    """POST na API; devolve a mensagem de erro (ou None se deu certo)."""
    try:
        resposta = httpx.post(
            f"{API_URL}{caminho}", json=corpo or {}, headers=HEADERS, timeout=TIMEOUT
        )
    except httpx.HTTPError as erro:
        return str(erro)
    if resposta.is_error:
        return str(resposta.json().get("detail", resposta.text))
    return None


def minutos(segundos: int) -> str:
    return f"{segundos // 60} min {segundos % 60:02d} s"


# ---------------------------------------------------------------- dashboard

ATUALIZACAO_SEGUNDOS = 15  # páginas do dashboard se atualizam sozinhas
COR_SERIE = "#2a78d6"  # série única (azul da paleta de referência)
COR_TEXTO_SECUNDARIO = "#52514e"
CANAIS_ROTULO = {"web": "💻 Web", "whatsapp": "📱 WhatsApp"}
ESTADOS_CURTOS = {
    "atendimento_ia": "🤖 IA",
    "confirmando_handoff": "❓ confirmando handoff",
    "aguardando_humano": "⏳ na fila",
    "atendimento_humano": "🧑‍💼 humano",
    "confirmando_retorno_ia": "❓ voltando p/ IA",
}
CLASSIFICACOES_ROTULO = {"quente": "🔥 quente", "morno": "🌤️ morno", "frio": "🧊 frio"}


def periodo_selecionado() -> dict[str, str]:
    """Período do dashboard (barra lateral; padrão: últimos 14 dias), como parâmetros da API.

    Guardado em session_state próprio: vale para todas as páginas."""
    hoje = date.today()
    inicio_padrao, fim_padrao = st.session_state.get(
        "periodo_dashboard", (hoje - timedelta(days=13), hoje)
    )
    with st.sidebar:
        escolhido = st.date_input(
            "Período",
            value=(inicio_padrao, fim_padrao),
            max_value=hoje,
            format="DD/MM/YYYY",
            key="periodo_widget",
        )
    if isinstance(escolhido, tuple) and len(escolhido) == 2:  # noqa: PLR2004
        st.session_state["periodo_dashboard"] = escolhido
        inicio_padrao, fim_padrao = escolhido
    local = datetime.now().astimezone().tzinfo
    inicio = datetime.combine(inicio_padrao, time.min, tzinfo=local)
    fim = datetime.combine(fim_padrao + timedelta(days=1), time.min, tzinfo=local)
    return {"inicio": inicio.isoformat(), "fim": fim.isoformat()}


def api_get_seguro(caminho: str, **params: Any) -> Any:
    """GET na API; em falha mostra o aviso e devolve None (a página segue de pé)."""
    try:
        return api_get(caminho, **params)
    except httpx.HTTPError as erro:
        st.warning(f"Sem conexão com a API ({caminho}): {erro}")
        return None


def barras(
    dados: list[dict[str, Any]],
    *,
    rotulos: dict[str, str] | None = None,
    valor: str = "total",
    categoria: str = "chave",
    rotulo_valor: str | None = None,
    altura_barra: int = 34,
) -> None:
    """Barras horizontais de UMA série (magnitude por categoria): uma cor, rótulo do valor
    na ponta, tooltip; sem legenda (o título da seção nomeia a série)."""
    if not dados:
        st.caption("Sem dados no período.")
        return
    linhas = [
        {
            "categoria": (rotulos or {}).get(str(d[categoria]), str(d[categoria])),
            "valor": d[valor],
            "texto": d.get("texto") or str(d[valor]),
        }
        for d in dados
    ]
    ordem = [linha["categoria"] for linha in linhas]
    dados_grafico = alt.Data(values=linhas)  # type: ignore[no-untyped-call]
    base = alt.Chart(dados_grafico).encode(
        y=alt.Y("categoria:N", sort=ordem, title=None, axis=alt.Axis(labelLimit=220)),
        x=alt.X("valor:Q", title=None, axis=alt.Axis(grid=False, labels=False, ticks=False)),
        tooltip=[
            alt.Tooltip("categoria:N", title="Categoria"),
            alt.Tooltip("valor:Q", title=rotulo_valor or "Total"),
        ],
    )
    grafico = base.mark_bar(
        color=COR_SERIE, cornerRadiusEnd=4, size=max(12, altura_barra - 14)
    ) + base.mark_text(align="left", dx=6, color=COR_TEXTO_SECUNDARIO).encode(text="texto:N")
    st.altair_chart(
        grafico.properties(height=altura_barra * len(linhas) + 10).configure_view(stroke=None),
        width="stretch",
    )


def duracao(segundos: float | None) -> str:
    if segundos is None:
        return "—"
    if segundos < 60:  # noqa: PLR2004
        return f"{segundos:.0f} s"
    if segundos < 3600:  # noqa: PLR2004
        return f"{segundos / 60:.1f} min"
    return f"{segundos / 3600:.1f} h"


def percentual(fracao: float | None) -> str:
    return "—" if fracao is None else f"{fracao:.0%}"
