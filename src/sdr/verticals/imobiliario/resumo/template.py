"""Resumo de handoff da imobiliária: o que o corretor (ou o especialista em investimentos)
precisa saber antes de atender o lead. Puro: só declara as seções; quem preenche e ancora
é o core."""

from sdr.core.domain.resumo import SecaoResumo, TemplateResumo, TipoSecao

TEMPLATE_RESUMO = TemplateResumo(
    versao="resumo_imobiliario_v1",
    titulo="Resumo do lead para o corretor",
    instrucoes=(
        "Quem lê é o corretor (ou o especialista em investimentos) que vai atender o lead "
        "na visita ou na reunião. Ele precisa chegar sabendo o que o lead procura, o que já "
        "viu, o que o preocupa e o que ainda falta responder."
    ),
    secoes=(
        SecaoResumo(
            "perfil",
            "Perfil do lead",
            TipoSecao.TEXTO,
            "Quem é e o que busca, em 1 ou 2 frases, só com o que ele contou (objetivo, "
            "momento de vida, para quem é o imóvel). Sem suposições.",
        ),
        SecaoResumo("necessidades", "Necessidades (ficha)", TipoSecao.FICHA),
        SecaoResumo("score", "Score e motivos", TipoSecao.SCORE),
        SecaoResumo(
            "imoveis_sugeridos",
            "Imóveis sugeridos e reação do lead",
            TipoSecao.ITENS_CATALOGO,
            "Cada imóvel apresentado na conversa e como o lead reagiu (gostou, pediu "
            "detalhes, achou caro, descartou...).",
        ),
        SecaoResumo(
            "objecoes",
            "Objeções e receios",
            TipoSecao.LISTA,
            "Preocupações que o lead manifestou: preço, região, financiamento, condomínio, "
            "prazo, segurança do investimento...",
        ),
        SecaoResumo(
            "perguntas_em_aberto",
            "Perguntas em aberto",
            TipoSecao.LISTA,
            "Perguntas do lead que ficaram sem resposta ou dependem do corretor.",
        ),
        SecaoResumo("agendamento", "Agendamento", TipoSecao.AGENDAMENTO),
        SecaoResumo(
            "proximo_passo",
            "Próximo passo recomendado",
            TipoSecao.TEXTO,
            "O que o corretor deve fazer/levar para o atendimento, a partir do que o lead "
            "disse (ex.: separar os imóveis que ele gostou, simular financiamento com FGTS).",
        ),
        SecaoResumo(
            "trechos_chave",
            "Trechos-chave da conversa",
            TipoSecao.TRECHOS,
            "De 2 a 4 falas do lead, literais, que mostram motivação, urgência ou restrições.",
        ),
    ),
)
