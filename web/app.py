"""Front Streamlit multipágina: dashboard da operação (visão geral, leads, fila, agenda,
follow-ups) e o chat de demonstração com a Lia.

Consome SOMENTE a API HTTP — nunca o banco. Cada página é um script em web/paginas/;
as peças comuns estão em web/comum.py.
"""

import streamlit as st

st.set_page_config(page_title="Lia — SDR Imobiliário", page_icon="🏠", layout="wide")

paginas = {
    "Dashboard": [
        st.Page("paginas/visao_geral.py", title="Visão geral", icon="📊", default=True),
        st.Page("paginas/leads.py", title="Leads", icon="👥"),
        st.Page("paginas/fila.py", title="Fila de atendimento", icon="🧑‍💼"),
        st.Page("paginas/agenda.py", title="Agenda", icon="📅"),
        st.Page("paginas/followups.py", title="Follow-ups", icon="⏰"),
    ],
    "Demonstração": [st.Page("paginas/chat.py", title="Chat com a Lia", icon="💬")],
}
st.navigation(paginas).run()
