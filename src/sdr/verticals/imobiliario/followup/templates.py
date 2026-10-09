"""Templates lógicos da imobiliária (mensagens ativas fora da janela de 24h do WhatsApp).

Puro: nome lógico, texto de referência para aprovação na Meta, variáveis nomeadas e a
regra de preenchimento de cada uma (a partir da ficha e do contexto). Qual template
aprovado responde por cada nome lógico é config da operação (WHATSAPP_TEMPLATES).
O LLM só escreve as variáveis "gancho" — e o core valida tudo, com fallback.

Os textos de referência viram docs/whatsapp-templates.md (`python -m sdr.cli templates-doc`).
"""

from sdr.core.domain.agenda import DIAS_SEMANA
from sdr.core.domain.template import (
    CategoriaTemplate,
    ContextoTemplate,
    TemplateLogico,
    VariavelTemplate,
)
from sdr.verticals.imobiliario.qualificacao.domain.regras import INVESTIMENTO

COMPROMISSOS = {
    "visita_imovel": "visita aos imóveis",
    "reuniao_especialista": "reunião sobre investimentos",
}
LOCAIS = {"presencial": "no imóvel", "online": "online", "escritorio": "no nosso escritório"}
PROXIMO_PASSO = {
    INVESTIMENTO: "conversar com a nossa especialista em investimentos",
}


def descrever_busca(ctx: ContextoTemplate) -> str | None:
    """Ficha → "apartamento de 2 quartos em Pinheiros" (só com o que o lead contou)."""
    ficha = ctx.ficha
    if ctx.intencao == INVESTIMENTO:
        regiao = ficha.get("regiao")
        return f"investimento em imóveis em {regiao}" if regiao else "investimento em imóveis"
    tipo = str(ficha.get("tipo_imovel") or "imóvel").replace("_", " ")
    partes = [tipo]
    if isinstance(quartos := ficha.get("quartos"), int):
        partes.append(f"de {quartos} quarto{'s' if quartos > 1 else ''}")
    if regiao := ficha.get("regiao"):
        partes.append(f"em {regiao}")
    return " ".join(partes) if len(partes) > 1 else None


def _quando(ctx: ContextoTemplate) -> str | None:
    if ctx.agendamento is None:
        return None
    local = ctx.agendamento.inicio.astimezone(ctx.fuso)
    hora = f"{local:%H}h" + (f"{local:%M}" if local.minute else "")
    return f"{DIAS_SEMANA[local.weekday()]}, {local:%d/%m}, às {hora}"


PRIMEIRO_NOME = VariavelTemplate(
    "primeiro_nome",
    "Primeiro nome do lead (perfil do WhatsApp); sem nome, uma saudação neutra.",
    padrao="tudo bem",
    preencher=lambda ctx: ctx.primeiro_nome,
    max_caracteres=40,
    exemplo="Ana",
)
BUSCA = VariavelTemplate(
    "busca",
    "Resumo da busca a partir da ficha: tipo de imóvel, quartos e região "
    '(ex.: "apartamento de 2 quartos em Pinheiros").',
    padrao="o seu novo imóvel",
    preencher=descrever_busca,
    max_caracteres=80,
    exemplo="apartamento de 2 quartos em Pinheiros",
)
GANCHO = VariavelTemplate(
    "gancho",
    "Uma frase curta (LLM) que retoma algo concreto da conversa, sem pressão e sem "
    "inventar; termina com ponto.",
    padrao="Separei um tempinho para continuar te ajudando.",
    gancho=True,
    max_caracteres=120,
    exemplo="Lembrei de você porque entraram opções perto do metrô.",
)

RETOMADA_DESCOBERTA = TemplateLogico(
    "imob_retomada_descoberta",
    CategoriaTemplate.MARKETING,
    "Oi, {{primeiro_nome}}! Aqui é a Lia, da imobiliária. Você ainda está procurando "
    "imóvel? Me conta se é para comprar, alugar ou investir que eu te ajudo por aqui.",
    (PRIMEIRO_NOME,),
)
RETOMADA_REGIAO = TemplateLogico(
    "imob_retomada_regiao",
    CategoriaTemplate.MARKETING,
    "Oi, {{primeiro_nome}}! A Lia de novo. {{gancho}} Se quiser, me diga em qual região "
    "você procura que eu já separo algumas opções.",
    (PRIMEIRO_NOME, GANCHO),
)
RETOMADA_BUSCA = TemplateLogico(
    "imob_retomada_busca",
    CategoriaTemplate.MARKETING,
    "Oi, {{primeiro_nome}}! Aqui é a Lia, da imobiliária, sobre a sua busca: {{busca}}. "
    "{{gancho}} Quer continuar de onde paramos?",
    (PRIMEIRO_NOME, BUSCA, GANCHO),
)
NOVIDADE_IMOVEL = TemplateLogico(
    "imob_novidade_imovel",
    CategoriaTemplate.MARKETING,
    "Oi, {{primeiro_nome}}! Apareceu uma opção que combina com a sua busca ({{busca}}): "
    "{{imovel_novo}}. Quer que eu te conte mais?",
    (
        PRIMEIRO_NOME,
        BUSCA,
        VariavelTemplate(
            "imovel_novo",
            "Código e título do imóvel novo compatível que o core encontrou no catálogo "
            '(ex.: "IMV-012, apartamento de 2 quartos na Vila Mariana").',
            padrao="um imóvel novo na nossa base",
            exemplo="IMV-012, apartamento de 2 quartos na Vila Mariana",
            preencher=lambda ctx: (
                f"{ctx.itens_novos[0].id}, {ctx.itens_novos[0].titulo}" if ctx.itens_novos else None
            ),
            max_caracteres=120,
        ),
    ),
)
CONVITE_VISITA = TemplateLogico(
    "imob_convite_visita",
    CategoriaTemplate.MARKETING,
    "Oi, {{primeiro_nome}}! Com o que você me contou sobre a sua busca ({{busca}}), o "
    "próximo passo é {{proximo_passo}}. Quer que eu veja um horário para você?",
    (
        PRIMEIRO_NOME,
        BUSCA,
        VariavelTemplate(
            "proximo_passo",
            "Pela intenção: compra/aluguel → conhecer os imóveis com um corretor; "
            "investimento → conversar com a especialista em investimentos.",
            padrao="conhecer os imóveis com um dos nossos corretores",
            exemplo="conversar com a nossa especialista em investimentos",
            preencher=lambda ctx: PROXIMO_PASSO.get(ctx.intencao or ""),
        ),
    ),
)
ENCERRAMENTO_BUSCA = TemplateLogico(
    "imob_encerramento_busca",
    CategoriaTemplate.MARKETING,
    "Oi, {{primeiro_nome}}! Vou parar de te mandar mensagens por aqui, tudo bem? Quando "
    "quiser retomar a busca, é só me chamar que eu continuo de onde paramos.",
    (PRIMEIRO_NOME,),
)
LEMBRETE_AGENDAMENTO = TemplateLogico(
    "imob_lembrete_agendamento",
    CategoriaTemplate.UTILITY,
    "Oi, {{primeiro_nome}}! Passando para lembrar da sua {{compromisso}} com "
    "{{com_quem}}, marcada para {{quando}} ({{onde}}). Continua de pé? Se precisar, eu "
    "remarco para você.",
    (
        PRIMEIRO_NOME,
        VariavelTemplate(
            "compromisso",
            "Tipo do agendamento: visita aos imóveis ou reunião sobre investimentos.",
            padrao="visita",
            exemplo="visita aos imóveis",
            preencher=lambda ctx: (
                COMPROMISSOS.get(ctx.agendamento.tipo) if ctx.agendamento else None
            ),
        ),
        VariavelTemplate(
            "com_quem",
            "Nome do corretor ou da especialista do agendamento.",
            padrao="a nossa equipe",
            exemplo="Rafael Souza",
            preencher=lambda ctx: ctx.agendamento.responsavel.nome if ctx.agendamento else None,
            max_caracteres=60,
        ),
        VariavelTemplate(
            "quando",
            'Dia da semana, data e hora no fuso da operação (ex.: "quinta, 15/10, às 14h").',
            padrao="o horário combinado",
            exemplo="quinta, 15/10, às 14h",
            preencher=_quando,
        ),
        VariavelTemplate(
            "onde",
            "Modalidade: no imóvel, online ou no nosso escritório.",
            padrao="como combinamos",
            exemplo="no imóvel",
            preencher=lambda ctx: (
                LOCAIS.get(ctx.agendamento.modalidade) if ctx.agendamento else None
            ),
        ),
    ),
)
RESPOSTA_EQUIPE = TemplateLogico(
    "imob_resposta_equipe",
    CategoriaTemplate.UTILITY,
    "Oi, {{primeiro_nome}}! Aqui é {{responsavel}}, da imobiliária. Tenho um retorno sobre "
    "o seu atendimento: pode me responder por aqui para continuarmos?",
    (
        PRIMEIRO_NOME,
        VariavelTemplate(
            "responsavel",
            "Nome de quem da equipe está atendendo (tela Fila).",
            padrao="a equipe de atendimento",
            exemplo="Rafael Souza",
            preencher=lambda ctx: ctx.responsavel,
            max_caracteres=60,
        ),
    ),
)

TEMPLATES: tuple[TemplateLogico, ...] = (
    RETOMADA_DESCOBERTA,
    RETOMADA_REGIAO,
    RETOMADA_BUSCA,
    NOVIDADE_IMOVEL,
    CONVITE_VISITA,
    ENCERRAMENTO_BUSCA,
    LEMBRETE_AGENDAMENTO,
    RESPOSTA_EQUIPE,
)
