"""Regras puras de qualificação do core: intenção, merge da ficha, faltantes e eventos."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from sdr.core.domain.eventos import TipoEvento
from sdr.core.domain.qualificacao import (
    INTENCAO_INDEFINIDA,
    Classificacao,
    Qualificacao,
    Score,
    campos_faltantes,
)

T0 = datetime(2026, 10, 6, 12, tzinfo=UTC)


def nova(**kw: object) -> Qualificacao:
    return Qualificacao(lead_id=uuid4(), **kw)  # type: ignore[arg-type]


def tipos(eventos: list) -> list[TipoEvento]:  # type: ignore[type-arg]
    return [e.tipo for e in eventos]


class TestIntencao:
    def test_primeira_intencao_definida_emite_identificada(self) -> None:
        q, eventos = nova().aplicar_intencao("plano", 0.9, T0)

        assert q.intencao_atual == "plano"
        assert q.fichas == {"plano": {}}
        assert tipos(eventos) == [TipoEvento.INTENCAO_IDENTIFICADA]
        assert eventos[0].payload == {"intencao": "plano", "confianca": 0.9}

    def test_indefinida_sem_intencao_anterior_segue_indefinida(self) -> None:
        q, eventos = nova().aplicar_intencao(INTENCAO_INDEFINIDA, 0.99, T0)
        assert (q.intencao_atual, eventos) == (None, [])

    def test_indefinida_com_intencao_anterior_mantem_a_atual(self) -> None:
        atual = nova(intencao_atual="plano", fichas={"plano": {"unidade": "Centro"}})
        q, eventos = atual.aplicar_intencao(INTENCAO_INDEFINIDA, 0.99, T0)
        assert (q, eventos) == (atual, [])

    def test_confianca_abaixo_do_limiar_nao_troca(self) -> None:
        atual = nova(intencao_atual="plano")
        q, eventos = atual.aplicar_intencao("avulso", 0.5, T0, limiar=0.6)
        assert (q.intencao_atual, eventos) == ("plano", [])

    def test_troca_emite_alterada_e_herda_campos_comuns_sem_apagar_a_ficha_antiga(self) -> None:
        atual = nova(
            intencao_atual="plano",
            fichas={"plano": {"unidade": "Centro", "orcamento": 200}, "avulso": {"data": "sáb"}},
        )

        q, eventos = atual.aplicar_intencao("avulso", 0.8, T0, campos_destino=("unidade", "data"))

        assert q.intencao_atual == "avulso"
        assert q.fichas["avulso"] == {"data": "sáb", "unidade": "Centro"}  # herdou só o vazio
        assert q.fichas["plano"] == {"unidade": "Centro", "orcamento": 200}  # preservada
        assert tipos(eventos) == [TipoEvento.INTENCAO_ALTERADA, TipoEvento.CAMPO_PREENCHIDO]
        assert eventos[0].payload == {"de": "plano", "para": "avulso", "confianca": 0.8}
        assert eventos[1].payload["origem"] == "herdado:plano"


class TestMergeDaFicha:
    BASE = nova(
        intencao_atual="plano", fichas={"plano": {"unidade": "Centro", "modalidades": ["yoga"]}}
    )

    def test_preenche_campos_vazios(self) -> None:
        q, eventos = self.BASE.aplicar_extracao({"horario": "noite", "orcamento": 150}, T0)

        assert q.ficha == {
            "unidade": "Centro",
            "modalidades": ["yoga"],
            "horario": "noite",
            "orcamento": 150,
        }
        assert tipos(eventos) == [TipoEvento.CAMPO_PREENCHIDO] * 2

    def test_valor_diferente_sem_correcao_explicita_nao_sobrescreve(self) -> None:
        q, eventos = self.BASE.aplicar_extracao({"unidade": "Paulista"}, T0)
        assert q.ficha["unidade"] == "Centro"
        assert eventos == []

    def test_correcao_explicita_sobrescreve_e_emite_corrigido(self) -> None:
        q, eventos = self.BASE.aplicar_extracao({"unidade": "Paulista"}, T0, corrigidos=["unidade"])
        assert q.ficha["unidade"] == "Paulista"
        assert tipos(eventos) == [TipoEvento.CAMPO_CORRIGIDO]
        assert eventos[0].payload == {
            "intencao": "plano",
            "campo": "unidade",
            "de": "Centro",
            "para": "Paulista",
        }

    @pytest.mark.parametrize("vazio", [None, "", [], {}])
    def test_vazio_nunca_apaga(self, vazio: object) -> None:
        q, eventos = self.BASE.aplicar_extracao({"unidade": vazio}, T0, corrigidos=["unidade"])
        assert q.ficha["unidade"] == "Centro"
        assert eventos == []

    def test_remocao_explicita_apaga_e_emite_removido(self) -> None:
        q, eventos = self.BASE.aplicar_extracao({"unidade": "Paulista"}, T0, removidos=["unidade"])
        assert "unidade" not in q.ficha
        assert tipos(eventos) == [TipoEvento.CAMPO_REMOVIDO]
        assert eventos[0].payload["valor_anterior"] == "Centro"

    def test_listas_acumulam_sem_duplicar(self) -> None:
        q, _ = self.BASE.aplicar_extracao({"modalidades": ["yoga", "pilates"]}, T0)
        assert q.ficha["modalidades"] == ["yoga", "pilates"]

    def test_lista_corrigida_e_substituida(self) -> None:
        q, _ = self.BASE.aplicar_extracao(
            {"modalidades": ["natação"]}, T0, corrigidos=["modalidades"]
        )
        assert q.ficha["modalidades"] == ["natação"]

    def test_sem_intencao_nao_ha_ficha(self) -> None:
        q, eventos = nova().aplicar_extracao({"unidade": "Centro"}, T0)
        assert (q.fichas, eventos) == ({}, [])

    def test_merge_so_toca_a_ficha_da_intencao_atual(self) -> None:
        base = nova(intencao_atual="plano", fichas={"plano": {}, "avulso": {"data": "sáb"}})
        q, _ = base.aplicar_extracao({"unidade": "Centro"}, T0)
        assert q.fichas["avulso"] == {"data": "sáb"}


class TestCamposFaltantes:
    def test_em_ordem_de_prioridade_ignorando_preenchidos_e_vazios(self) -> None:
        ficha = {"unidade": "Centro", "horario": "", "modalidades": []}
        prioridade = ("orcamento", "unidade", "horario", "modalidades")
        assert campos_faltantes(ficha, prioridade) == ["orcamento", "horario", "modalidades"]

    def test_campos_fora_da_prioridade_nao_contam(self) -> None:
        assert campos_faltantes({}, ("a",)) == ["a"]


class TestScore:
    MORNO = Score(50, Classificacao.MORNO, ("2 campos",))
    QUENTE = Score(75, Classificacao.QUENTE, ("3 campos",))

    def test_primeiro_score_emite_alterado(self) -> None:
        q, eventos = nova(intencao_atual="plano").aplicar_score(
            self.MORNO, False, False, "agendar_aula", T0
        )
        assert q.score == self.MORNO
        assert q.proxima_acao is None
        assert tipos(eventos) == [TipoEvento.SCORE_ALTERADO]
        assert eventos[0].payload["de"] is None
        assert eventos[0].payload["motivos"] == ["2 campos"]

    def test_score_igual_nao_emite(self) -> None:
        base = nova(intencao_atual="plano", score=self.MORNO)
        _, eventos = base.aplicar_score(self.MORNO, False, False, "agendar_aula", T0)
        assert eventos == []

    def test_qualificar_emite_uma_vez_e_marca_proxima_acao(self) -> None:
        base = nova(intencao_atual="plano", score=self.MORNO)

        q, eventos = base.aplicar_score(self.QUENTE, True, False, "agendar_aula", T0)
        _, de_novo = q.aplicar_score(self.QUENTE, True, True, "agendar_aula", T0)

        assert q.proxima_acao == "agendar_aula"
        assert q.qualificado_em == T0
        assert tipos(eventos) == [TipoEvento.SCORE_ALTERADO, TipoEvento.LEAD_QUALIFICADO]
        assert eventos[1].payload == {
            "intencao": "plano",
            "score": 75,
            "classificacao": "quente",
            "proxima_acao": "agendar_aula",
        }
        assert de_novo == []

    def test_deixar_de_estar_qualificado_limpa_proxima_acao(self) -> None:
        base = nova(intencao_atual="plano", score=self.QUENTE, proxima_acao="agendar_aula")
        q, _ = base.aplicar_score(self.MORNO, False, True, "agendar_aula", T0)
        assert q.proxima_acao is None
