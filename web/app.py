"""Front Streamlit. Consome SOMENTE a API HTTP — nunca o banco."""

import os

import httpx
import streamlit as st

API_URL = os.environ.get("API_URL", "http://localhost:8000")

st.set_page_config(page_title="SDR Imobiliário", page_icon="🏠")
st.title("🏠 SDR Imobiliário")
st.caption(f"API: {API_URL}")

try:
    resposta = httpx.get(f"{API_URL}/health", timeout=5)
    corpo = resposta.json()
except (httpx.HTTPError, ValueError) as erro:
    st.error(f"API indisponível: {erro}")
else:
    if corpo.get("status") == "ok":
        st.success("API e banco operacionais")
    else:
        st.warning("API respondeu, mas há componentes degradados")
    st.json(corpo)

st.info("O chat com a Lia chega na Etapa C.")
