"""RastreioPort com Langfuse (SDK v4, sobre OpenTelemetry). Único módulo que importa o SDK.

- Um trace por unidade de trabalho (turno, follow-up, resumo): session_id = lead_id,
  user_id = hash do lead, tags e metadata com canal, vertical, estado de atendimento,
  intenção e versões dos prompts.
- Etapas (nós do grafo) e gerações (LLM: modelo, tokens; o Langfuse calcula custo pela
  tabela de preços do modelo e a latência pelos tempos da observação).
- PII: toda entrada/saída/metadata passa por `mascarar_dados` (telefone, e-mail, CPF)
  antes de sair do processo.
- Falha do Langfuse nunca derruba o fluxo: o SDK exporta em segundo plano.
"""

import hashlib
import logging
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

from langfuse import Langfuse, propagate_attributes

from sdr.core.application.ports.observabilidade import ContextoRastro, Observacao, Rastro
from sdr.core.domain.pii import mascarar_pii

logger = logging.getLogger(__name__)

LIMITE_METADATA = 200  # o Langfuse exige valores de metadata propagada com até 200 caracteres


def mascarar_dados(*, data: Any, **_: Any) -> Any:
    """MaskFunction do Langfuse: mascara PII em qualquer estrutura serializável."""
    if isinstance(data, str):
        return mascarar_pii(data)
    if isinstance(data, Mapping):
        return {k: mascarar_dados(data=v) for k, v in data.items()}
    if isinstance(data, list | tuple):
        return [mascarar_dados(data=v) for v in data]
    return data


def usuario_do_lead(lead_id: object) -> str:
    """user_id estável e sem dado pessoal: hash do lead."""
    return hashlib.sha256(str(lead_id).encode()).hexdigest()[:16]


def _metadata(valores: Mapping[str, object]) -> dict[str, str]:
    return {k: mascarar_pii(str(v))[:LIMITE_METADATA] for k, v in valores.items() if v is not None}


class RastreioLangfuse:
    def __init__(self, cliente: Langfuse, *, metadados_fixos: Mapping[str, str]) -> None:
        self._lf = cliente
        self._fixos = dict(metadados_fixos)
        self._prefixo_url: str | None = None

    @classmethod
    def criar(
        cls,
        *,
        public_key: str,
        secret_key: str,
        host: str,
        ambiente: str,
        metadados_fixos: Mapping[str, str],
    ) -> "RastreioLangfuse":
        cliente = Langfuse(
            public_key=public_key,
            secret_key=secret_key,
            base_url=host,
            environment=ambiente,
            mask=mascarar_dados,
        )
        return cls(cliente, metadados_fixos=metadados_fixos)

    def preparar(self) -> None:
        """Descobre o endereço dos traces no projeto (uma chamada HTTP; rodar fora do loop)."""
        try:
            exemplo = self._lf.get_trace_url(trace_id="0" * 32)
        except Exception:
            logger.warning("Langfuse: não foi possível montar o link dos traces", exc_info=True)
            return
        if exemplo:
            self._prefixo_url = exemplo.removesuffix("0" * 32)

    def _url(self, trace_id: str | None) -> str | None:
        return f"{self._prefixo_url}{trace_id}" if self._prefixo_url and trace_id else None

    @contextmanager
    def rastrear(self, contexto: ContextoRastro) -> Iterator[Rastro]:
        intencao = contexto.intencao or "indefinida"
        tags = [
            f"canal:{contexto.canal}",
            f"atendimento:{contexto.estado_atendimento}",
            f"intencao:{intencao}",
            *(f"{k}:{v}" for k, v in self._fixos.items() if k == "vertical"),
        ]
        metadata = _metadata(
            {
                **self._fixos,
                "canal": contexto.canal,
                "estado_atendimento": contexto.estado_atendimento,
                "intencao": intencao,
                **contexto.metadados,
            }
        )
        with (
            self._lf.start_as_current_observation(
                name=contexto.nome, as_type="span", input=contexto.entrada
            ) as raiz,
            propagate_attributes(
                user_id=usuario_do_lead(contexto.lead_id),
                session_id=str(contexto.lead_id),
                tags=tags,
                metadata=metadata,
                trace_name=contexto.nome,
            ),
        ):
            rastro = Rastro(trace_id=raiz.trace_id, url=self._url(raiz.trace_id))
            try:
                yield rastro
            except Exception as erro:
                raiz.update(level="ERROR", status_message=str(erro)[:LIMITE_METADATA])
                raise
            finally:
                raiz.update(
                    input=rastro.entrada if rastro.entrada is not None else contexto.entrada,
                    output=rastro.saida,
                    metadata=_metadata(rastro.metadados_finais) or None,
                )

    @contextmanager
    def etapa(
        self, nome: str, *, tipo: str = "span", entrada: object = None
    ) -> Iterator[Observacao]:
        observacao = Observacao()
        with self._lf.start_as_current_observation(
            name=nome,
            as_type="tool" if tipo == "tool" else "span",
            input=entrada,
        ) as span:
            try:
                yield observacao
            finally:
                span.update(
                    output=observacao.saida,
                    level="ERROR" if observacao.erro else None,
                    status_message=observacao.erro,
                )

    @contextmanager
    def geracao(
        self,
        nome: str,
        *,
        modelo: str,
        entrada: object,
        parametros: Mapping[str, object] | None = None,
    ) -> Iterator[Observacao]:
        observacao = Observacao(modelo=modelo)
        with self._lf.start_as_current_observation(
            name=nome,
            as_type="generation",
            model=modelo,
            input=entrada,
            model_parameters={k: _parametro(v) for k, v in (parametros or {}).items()},
        ) as geracao:
            try:
                yield observacao
            finally:
                geracao.update(
                    output=observacao.saida,
                    model=observacao.modelo or modelo,
                    usage_details={
                        "input": observacao.tokens_entrada,
                        "output": observacao.tokens_saida,
                    },
                    level="ERROR" if observacao.erro else None,
                    status_message=observacao.erro,
                )

    def encerrar(self) -> None:
        try:
            self._lf.shutdown()  # envia o que estiver pendente
        except Exception:
            logger.warning("Langfuse: falha ao enviar os últimos traces", exc_info=True)


def _parametro(valor: object) -> str | int | float | bool | list[str] | None:
    if valor is None or isinstance(valor, str | int | float | bool):
        return valor
    if isinstance(valor, list | tuple):
        return [str(v) for v in valor]
    return str(valor)
