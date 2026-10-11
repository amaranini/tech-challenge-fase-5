"""Lead, Conversa e Mensagem — o coração genérico de um SDR conversacional."""

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sdr.core.domain.agenda import NegociacaoAgenda
from sdr.core.domain.atendimento import Atendimento
from sdr.core.domain.qualificacao import Qualificacao


def agora() -> datetime:
    return datetime.now(UTC)


class Canal(StrEnum):
    WEB = "web"
    WHATSAPP = "whatsapp"


class Papel(StrEnum):
    """Autor da mensagem."""

    LEAD = "lead"
    ASSISTENTE = "assistente"  # a IA
    RESPONSAVEL = "responsavel"  # pessoa da equipe (atendimento humano)


class StatusMensagem(StrEnum):
    PENDENTE = "pendente"  # do lead, aguardando o turno ser processado
    PROCESSADA = "processada"  # do lead, já respondida
    FALHA = "falha"  # do lead, o turno falhou (fica no histórico, sem resposta)
    ENVIADA = "enviada"  # do assistente ou do responsável, entregue ao canal
    NAO_ENVIADA = "nao_enviada"  # bloqueada antes do canal (ex.: sem template fora da janela)


class StatusEntrega(StrEnum):
    """O que o provedor do canal informou sobre uma mensagem que saiu (callback de status)."""

    ENVIADA = "enviada"
    ENTREGUE = "entregue"
    LIDA = "lida"
    FALHOU = "falhou"

    @property
    def ordem(self) -> int:
        return _ORDEM_ENTREGA[self]


_ORDEM_ENTREGA = {
    StatusEntrega.ENVIADA: 1,
    StatusEntrega.ENTREGUE: 2,
    StatusEntrega.LIDA: 3,
    StatusEntrega.FALHOU: 4,
}


def avancar_entrega(atual: StatusEntrega | None, novo: StatusEntrega) -> StatusEntrega:
    """Callbacks chegam fora de ordem: o status de uma parte nunca regride (falha é final)."""
    if atual is None:
        return novo
    return novo if novo.ordem > atual.ordem else atual


def entrega_da_mensagem(partes: "list[StatusEntrega]") -> StatusEntrega | None:
    """Mensagem dividida em partes: falhou se alguma falhou; senão, o status da mais atrasada."""
    if not partes:
        return None
    if StatusEntrega.FALHOU in partes:
        return StatusEntrega.FALHOU
    return min(partes, key=lambda s: s.ordem)


class StatusConversa(StrEnum):
    ABERTA = "aberta"
    ENCERRADA = "encerrada"


@dataclass(frozen=True)
class Lead:
    """Pessoa atendida, identificada pelo canal + id do remetente nesse canal.

    A ficha de qualificação (dentro de `qualificacao`) é opaca para o core: os campos e o
    schema são da vertical.
    """

    id: UUID
    canal: Canal
    remetente_id: str
    criado_em: datetime
    qualificacao: Qualificacao
    nome: str | None = None
    agenda: NegociacaoAgenda = field(default_factory=NegociacaoAgenda)
    atendimento: Atendimento | None = None  # None = ATENDIMENTO_IA desde sempre
    opt_out_em: datetime | None = None  # pediu para não receber mais mensagens ativas

    @property
    def atendimento_atual(self) -> Atendimento:
        return self.atendimento or Atendimento(self.id)

    @classmethod
    def novo(cls, canal: Canal, remetente_id: str, nome: str | None = None) -> "Lead":
        if not remetente_id.strip():
            raise ValueError("remetente_id é obrigatório")
        lead_id = uuid4()
        return cls(
            id=lead_id,
            canal=canal,
            remetente_id=remetente_id,
            criado_em=agora(),
            qualificacao=Qualificacao(lead_id),
            nome=nome,
            atendimento=Atendimento(lead_id),
        )


@dataclass(frozen=True)
class Conversa:
    id: UUID
    lead_id: UUID
    canal: Canal
    iniciada_em: datetime
    atualizada_em: datetime
    status: StatusConversa = StatusConversa.ABERTA

    @classmethod
    def nova(cls, lead: Lead) -> "Conversa":
        momento = agora()
        return cls(
            id=uuid4(),
            lead_id=lead.id,
            canal=lead.canal,
            iniciada_em=momento,
            atualizada_em=momento,
        )

    def tocar(self, momento: datetime) -> "Conversa":
        return replace(self, atualizada_em=momento)


@dataclass(frozen=True)
class Mensagem:
    id: UUID
    conversa_id: UUID
    papel: Papel
    texto: str
    criada_em: datetime
    metadados: Mapping[str, object] = field(default_factory=dict)
    status: StatusMensagem = StatusMensagem.PROCESSADA
    id_externo: str | None = None  # id no provedor (recebida: idempotência do webhook)
    entrega: StatusEntrega | None = None  # saída: o que o provedor informou

    @classmethod
    def nova(
        cls,
        conversa_id: UUID,
        papel: Papel,
        texto: str,
        *,
        metadados: Mapping[str, object] | None = None,
        criada_em: datetime | None = None,
        status: StatusMensagem | None = None,
        id_externo: str | None = None,
    ) -> "Mensagem":
        padrao = StatusMensagem.PROCESSADA if papel is Papel.LEAD else StatusMensagem.ENVIADA
        return cls(
            id=uuid4(),
            conversa_id=conversa_id,
            papel=papel,
            texto=texto,
            criada_em=criada_em or agora(),
            metadados=dict(metadados or {}),
            status=status or padrao,
            id_externo=id_externo,
        )
