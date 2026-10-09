"""Validação do X-Twilio-Signature (HMAC-SHA1 da URL pública + parâmetros do POST).

Atrás de túnel/proxy a URL que a aplicação enxerga difere da que o Twilio chamou, por
isso a URL é montada com PUBLIC_BASE_URL (config) + caminho + query.
Algoritmo: https://www.twilio.com/docs/usage/webhooks/webhooks-security
"""

import base64
import hashlib
import hmac
from collections.abc import Iterable


def calcular_assinatura(url: str, parametros: Iterable[tuple[str, str]], auth_token: str) -> str:
    """URL completa + cada nome e valor, em ordem alfabética, concatenados; HMAC-SHA1 com o
    auth token; base64."""
    dados = url + "".join(f"{nome}{valor}" for nome, valor in sorted(set(parametros)))
    digest = hmac.new(auth_token.encode(), dados.encode(), hashlib.sha1).digest()
    return base64.b64encode(digest).decode()


def assinatura_valida(
    url: str, parametros: Iterable[tuple[str, str]], auth_token: str, assinatura: str | None
) -> bool:
    if not assinatura or not auth_token:
        return False
    esperada = calcular_assinatura(url, parametros, auth_token)
    return hmac.compare_digest(esperada, assinatura)


def url_publica(base_publica: str, caminho: str, query: str = "") -> str:
    return base_publica.rstrip("/") + caminho + (f"?{query}" if query else "")
