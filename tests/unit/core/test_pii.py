import pytest

from sdr.core.domain.pii import mascarar_pii, mascarar_telefone


def test_mascara_telefone_e164_mantendo_ddi_ddd_e_final() -> None:
    assert mascarar_telefone("+5511987654321") == "+55119****4321"
    assert mascarar_telefone("whatsapp:+5511987654321") == "55119****4321"
    assert mascarar_telefone("123") == "123"


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("lead whatsapp:+5511987654321 criado", "lead whatsapp:+55119****4321 criado"),
        (
            "GET /conversas/whatsapp:+5511987654321/mensagens",
            "GET /conversas/whatsapp:+55119****4321/mensagens",
        ),
        ("GET /leads/whatsapp%3A%2B5511987654321", "GET /leads/whatsapp%3A%2B55119****4321"),
        ("ligue (11) 98765-4321", "ligue (119****4321"),
        ("cpf 123.456.789-09 ok", "cpf ***.***.***-** ok"),
        ("email ana.souza@gmail.com", "email a***@gmail.com"),
        ("R$ 800.000 em 2026-10-08 às 14:00", "R$ 800.000 em 2026-10-08 às 14:00"),
        ("lead 3f2b8c1e-1d2a-4b5c-9d8e-123456789abc", "lead 3f2b8c1e-1d2a-4b5c-9d8e-123456789abc"),
    ],
)
def test_mascara_pii_em_texto_livre(texto: str, esperado: str) -> None:
    assert mascarar_pii(texto) == esperado
