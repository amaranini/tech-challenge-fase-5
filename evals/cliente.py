"""Cliente da API no ar (local ou nuvem) para os roteiros: fala do lead, espera a Lia,
passos da equipe e leitura do estado. Usado pelo runner (evals/runner.py) e pelos testes
com LLM real (tests/apoio/api_viva.py).

Rotas da equipe exigem X-API-Key: a chave vem de API_KEY ou da 1ª de ADMIN_API_KEYS.
"""

import os
import time
from dataclasses import dataclass
from typing import Any

import httpx

from sdr.config.settings import obter_settings


class TurnoFalhouError(RuntimeError):
    """O turno terminou em FALHA (LLM indisponível?) — veja os logs da API."""


def chave_admin() -> str:
    if chave := os.environ.get("API_KEY"):
        return chave
    segredo = obter_settings().admin_api_keys
    return segredo.get_secret_value().split(",")[0].strip() if segredo else ""


@dataclass
class Turno:
    """Uma fala do lead e o que voltou dela."""

    texto: str
    resposta: dict[str, Any] | None  # mensagem do assistente (None = silêncio)
    latencia_s: float  # do POST até a resposta visível (inclui o debounce)
    historico: dict[str, Any]


class ApiViva:
    def __init__(self, url: str | None = None, chave: str | None = None) -> None:
        self.url = (url or os.environ.get("API_URL") or "http://localhost:8000").rstrip("/")
        chave = chave if chave is not None else chave_admin()
        self.headers = {"X-API-Key": chave} if chave else {}
        self._http = httpx.Client(base_url=self.url, headers=self.headers, timeout=30)

    def fechar(self) -> None:
        self._http.close()

    def no_ar(self) -> bool:
        try:
            return self._http.get("/health", timeout=5).is_success
        except httpx.HTTPError:
            return False

    # ------------------------------------------------------------------ lead
    def postar(self, lead_id: str, *textos: str) -> None:
        for texto in textos:
            r = self._http.post("/conversas/mensagens", json={"lead_id": lead_id, "texto": texto})
            if r.status_code != httpx.codes.ACCEPTED:
                raise AssertionError(f"POST /conversas/mensagens → {r.status_code}: {r.text}")

    def aguardar(self, lead_id: str, timeout: float = 120) -> dict[str, Any]:
        """Polling até não haver turno pendente; devolve o histórico."""
        limite = time.monotonic() + timeout
        while time.monotonic() < limite:
            historico: dict[str, Any] = self.historico(lead_id)
            if not historico["processando"]:
                if historico["mensagens"] and historico["mensagens"][-1]["status"] == "falha":
                    raise TurnoFalhouError(lead_id)
                return historico
            time.sleep(1)
        raise AssertionError(f"sem resposta em {timeout}s")

    def falar(self, lead_id: str, *textos: str) -> Turno:
        """Uma ou mais falas seguidas (mensagens picadas) → um turno da Lia."""
        inicio = time.monotonic()
        self.postar(lead_id, *textos)
        historico = self.aguardar(lead_id)
        ultima = historico["mensagens"][-1]
        resposta = ultima if ultima["papel"] == "assistente" else None
        return Turno(" / ".join(textos), resposta, time.monotonic() - inicio, historico)

    # ------------------------------------------------------------------ leitura
    def historico(self, lead_id: str) -> dict[str, Any]:
        r = self._http.get(f"/conversas/{lead_id}/mensagens")
        r.raise_for_status()
        corpo: dict[str, Any] = r.json()
        return corpo

    def lead(self, lead_id: str) -> dict[str, Any]:
        r = self._http.get(f"/leads/{lead_id}")
        r.raise_for_status()
        corpo: dict[str, Any] = r.json()
        return corpo

    def resumo(self, lead_id: str) -> dict[str, Any] | None:
        r = self._http.get(f"/leads/{lead_id}/resumo")
        if r.status_code == httpx.codes.NOT_FOUND:
            return None
        r.raise_for_status()
        corpo: dict[str, Any] = r.json()
        return corpo

    def aguardar_resumo(
        self, lead_id: str, gatilho: str | None, timeout: float = 120
    ) -> dict[str, Any]:
        """O resumo é gerado FORA do turno: espera a versão do gatilho esperado."""
        limite = time.monotonic() + timeout
        while time.monotonic() < limite:
            corpo = self.resumo(lead_id)
            if corpo is not None and (gatilho is None or corpo["gatilho"] == gatilho):
                return corpo
            time.sleep(2)
        raise AssertionError(f"sem resumo ({gatilho}) para {lead_id} em {timeout}s")

    def item(self, codigo: str) -> dict[str, Any] | None:
        """Item do catálogo da vertical (None = não existe)."""
        r = self._http.get(f"/imoveis/{codigo}")
        if r.status_code == httpx.codes.NOT_FOUND:
            return None
        r.raise_for_status()
        corpo: dict[str, Any] = r.json()
        return corpo

    # ------------------------------------------------------------------ equipe / demo
    def acao_da_equipe(self, lead_id: str, acao: dict[str, Any]) -> str | None:
        """Passo da tela Fila (assumir, responder, devolver) ou da demo (simular
        inatividade). Devolve o follow-up enviado, quando houver."""
        match acao["acao"]:
            case "assumir":
                r = self._http.post(
                    f"/atendimentos/{lead_id}/assumir", json={"responsavel": acao["responsavel"]}
                )
            case "responder":
                r = self._http.post(
                    f"/atendimentos/{lead_id}/mensagens", json={"texto": acao["texto"]}
                )
            case "devolver":
                r = self._http.post(f"/atendimentos/{lead_id}/devolver")
            case "simular_inatividade":
                antes = _do_assistente(self.historico(lead_id))
                r = self._http.post(f"/demo/leads/{lead_id}/simular-inatividade")
                if acao.get("espera") == "recusa":
                    if r.status_code != httpx.codes.CONFLICT:
                        raise AssertionError(f"esperava recusa (opt-out): {r.status_code} {r.text}")
                    return None
                if not r.is_success:
                    raise AssertionError(r.text)
                return self._aguardar_followup(lead_id, antes)
            case outra:
                raise ValueError(f"ação desconhecida: {outra}")
        if not r.is_success:
            raise AssertionError(f"{acao['acao']} → {r.status_code}: {r.text}")
        return None

    def _aguardar_followup(self, lead_id: str, antes: int, timeout: float = 120) -> str:
        """O worker envia o follow-up na próxima varredura: espera a mensagem nova."""
        limite = time.monotonic() + timeout
        while time.monotonic() < limite:
            historico = self.historico(lead_id)
            if _do_assistente(historico) > antes:
                texto: str = historico["mensagens"][-1]["texto"]
                return texto
            time.sleep(2)
        raise AssertionError(f"o worker não enviou o follow-up em {timeout}s (worker no ar?)")


def _do_assistente(historico: dict[str, Any]) -> int:
    return sum(1 for m in historico["mensagens"] if m["papel"] == "assistente")
