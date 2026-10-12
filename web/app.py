"""Front Streamlit multipágina: dashboard da operação (visão geral, leads, fila, agenda,
follow-ups) e o chat de demonstração com a Lia.

Consome SOMENTE a API HTTP — nunca o banco. Cada página é um script em web/paginas/;
as peças comuns estão em web/comum.py.
"""

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # raiz: pacote "web"

from web.autenticacao import botao_sair, exigir_login

st.set_page_config(page_title="Lia — SDR Imobiliário", page_icon="🏠", layout="wide")
exigir_login()  # todas as páginas (inclusive o chat de demonstração) exigem login
botao_sair()

paginas = {
    "Dashboard": [
        st.Page("paginas/visao_geral.py", title="Visão geral", icon="📊", default=True),
        st.Page("paginas/leads.py", title="Leads", icon="👥"),
        st.Page("paginas/fila.py", title="Fila de atendimento", icon="🧑‍💼"),
        st.Page("paginas/agenda.py", title="Agenda", icon="📅"),
        st.Page("paginas/followups.py", title="Follow-ups", icon="⏰"),
        st.Page("paginas/qualidade.py", title="Qualidade da IA", icon="🧪"),
    ],
    "Demonstração": [st.Page("paginas/chat.py", title="Chat com a Lia", icon="💬")],
}
st.navigation(paginas).run()
