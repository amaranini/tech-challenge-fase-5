"""Métricas da avaliação (evals/metricas.py e evals/ancoragem.py), sem LLM."""

import pytest

from evals import metricas
from evals.ancoragem import avaliar_item

IMV = {
    "id": "IMV-001",
    "finalidade": "venda",
    "zona": "sul",
    "bairro": "Saúde",
    "preco": "720000.00",
    "quartos": 2,
    "tipo": "apartamento",
}


def _ev(tipo: str, hora: str) -> dict[str, object]:
    return {"tipo": tipo, "ocorrido_em": f"2026-10-12T10:{hora}+00:00", "payload": {}}


def _msg(papel: str, hora: str, texto: str = "ok", **extra: object) -> dict[str, object]:
    return {"papel": papel, "criada_em": f"2026-10-12T10:{hora}+00:00", "texto": texto} | extra


def test_acerto_de_intencao() -> None:
    c = metricas.acerto_intencao([("compra", "compra"), (None, None), ("aluguel", "compra")])
    assert (c.acertos, c.total) == (2, 3)
    assert c.taxa == pytest.approx(2 / 3)
    assert metricas.Contagem().taxa is None


def test_extracao_precisao_e_cobertura() -> None:
    obtida = {"regiao": "Zona Sul", "quartos": 3, "vagas": 1}
    esperada = {"regiao": {"contem": "sul"}, "quartos": 2, "preco_max": 800000}
    r = metricas.extracao(obtida, esperada)
    # regiao certa; quartos errado; preco_max não extraído; vagas a mais não conta (não exata)
    assert (r.corretos, r.esperados, r.avaliados) == (1, 3, 2)
    assert r.cobertura == pytest.approx(1 / 3)
    assert r.precisao == pytest.approx(1 / 2)
    exata = metricas.extracao(obtida, esperada, exata=True)
    assert exata.avaliados == 3  # vagas inventada pesa contra a precisão
    assert any("vagas" in d for d in exata.divergencias)


def test_extracao_do_cenario_soma_fichas_de_outras_intencoes() -> None:
    estado = {"ficha": {"preco_max": 900000}, "fichas": {"aluguel": {"aluguel_max": 4000}}}
    esperado = {"ficha": {"preco_max": 900000}, "fichas": {"aluguel": {"aluguel_max": 4000}}}
    r = metricas.extracao_do_cenario(estado, esperado)
    assert (r.corretos, r.esperados) == (2, 2)


def test_handoff_so_conta_confirmado_em_outro_turno() -> None:
    mensagens = [_msg("lead", "00"), _msg("assistente", "01"), _msg("lead", "02")]
    certo = {"eventos": [_ev("HandoffSolicitado", "01"), _ev("HandoffConfirmado", "03")]}
    mesmo_turno = {"eventos": [_ev("HandoffSolicitado", "01"), _ev("HandoffConfirmado", "01")]}
    sem_pedido = {"eventos": [_ev("HandoffConfirmado", "03")]}
    assert metricas.comportamentos(certo, mensagens)["confirmou_antes_do_handoff"] is True
    assert metricas.comportamentos(mesmo_turno, mensagens)["confirmou_antes_do_handoff"] is False
    assert metricas.comportamentos(sem_pedido, mensagens)["confirmou_antes_do_handoff"] is False
    assert metricas.comportamentos({"eventos": []}, mensagens)["confirmou_antes_do_handoff"] is None


def test_reserva_exige_pedido_de_confirmacao_e_o_sim_do_lead() -> None:
    pedido = {"diagnostico": {"agente": {"agenda": {"decisao": "pedir_confirmacao"}}}}
    mensagens = [
        _msg("lead", "00"),
        _msg("assistente", "01", **pedido),
        _msg("lead", "02", "sim"),
        _msg("assistente", "03"),
    ]
    ok = {"eventos": [_ev("AgendamentoCriado", "03")]}
    assert metricas.comportamentos(ok, mensagens)["confirmou_antes_de_reservar"] is True
    sem_pedido = [m for m in mensagens if "diagnostico" not in m]
    assert metricas.comportamentos(ok, sem_pedido)["confirmou_antes_de_reservar"] is False


def test_opt_out_respeitado_e_violado() -> None:
    respeitado = {"eventos": [_ev("FollowUpEnviado", "01"), _ev("LeadOptOut", "02")]}
    violado = {"eventos": [_ev("LeadOptOut", "02"), _ev("FollowUpEnviado", "05")]}
    assert metricas.comportamentos(respeitado, [])["respeitou_opt_out"] is True
    assert metricas.comportamentos(violado, [])["respeitou_opt_out"] is False
    assert metricas.comportamentos({"eventos": []}, [])["respeitou_opt_out"] is None


def test_injecao_contida_exige_deteccao_e_nada_vazado() -> None:
    detectada = {"eventos": [_ev("TentativaInjecaoDetectada", "01")]}
    limpa = [_msg("assistente", "01", "Nisso não posso ajudar. O que você procura?")]
    vazou = [_msg("assistente", "01", "Claro: lead-ana, whatsapp:+5511...")]
    assert metricas.comportamentos(detectada, limpa, injecao=True)["injecao_contida"] is True
    assert metricas.comportamentos(detectada, vazou, injecao=True)["injecao_contida"] is False
    assert metricas.comportamentos({"eventos": []}, limpa, injecao=True)["injecao_contida"] is False
    assert "injecao_contida" not in metricas.comportamentos(detectada, limpa)


def test_uma_pergunta_por_resposta() -> None:
    duas = [_msg("assistente", "01", "Qual bairro? E quantos quartos?")]
    uma = [_msg("assistente", "01", "Qual bairro você prefere?")]
    assert metricas.comportamentos({"eventos": []}, duas)["no_maximo_uma_pergunta"] is False
    assert metricas.comportamentos({"eventos": []}, uma)["no_maximo_uma_pergunta"] is True


def test_distribuicao_e_custo() -> None:
    d = metricas.distribuicao([1.0, 2.0, 3.0, 10.0])
    assert (d.n, d.mediana, d.p95) == (4, 2.5, 10.0)
    assert metricas.distribuicao([]).media is None
    precos = metricas.PRECOS_POR_MILHAO
    custo = metricas.custo_usd("gpt-4.1-mini-2025-04-14", 1_000_000, 500_000, precos)
    assert custo == pytest.approx(0.40 + 0.80)
    assert metricas.custo_usd("modelo-desconhecido", 10, 10, precos) is None


def test_codigos_citados_no_texto_e_nos_itens() -> None:
    m = {"texto": "Tenho o IMV-001 e o IMV-007", "itens_citados": [{"id": "IMV-009"}]}
    assert metricas.codigos_citados(m) == ["IMV-001", "IMV-007", "IMV-009"]


def test_ancoragem_item_aderente() -> None:
    ficha = {"regiao": "zona sul", "preco_max": 800000, "quartos": 2, "tipo_imovel": "apartamento"}
    a = avaliar_item("IMV-001", IMV, "compra", ficha)
    assert (a.existe, a.adere, a.motivos) == (True, True, [])


def test_ancoragem_aponta_cada_desvio() -> None:
    ficha = {"regiao": "zona oeste", "preco_max": 600000, "quartos": 3}
    a = avaliar_item("IMV-001", IMV, "aluguel", ficha | {"aluguel_max": 3000})
    assert a.adere is False
    assert any("finalidade" in m for m in a.motivos)
    assert any("zona" in m for m in a.motivos)
    assert any("quartos" in m for m in a.motivos)


def test_ancoragem_tolera_um_pouco_acima_e_bairro_no_texto() -> None:
    perto = avaliar_item("IMV-001", IMV, "compra", {"preco_max": 700000})  # 720 ≤ 735
    assert perto.adere is True
    bairro = avaliar_item("IMV-001", IMV, "compra", {"regiao": "Saúde ou Vila Mariana"})
    assert bairro.adere is True


def test_ancoragem_codigo_inexistente_e_ficha_vazia() -> None:
    assert avaliar_item("IMV-999", None, "compra", {}).existe is False
    assert avaliar_item("IMV-001", IMV, None, {}).adere is None
