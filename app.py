"""HargaIkan v2 home page: intro text and the legend of the four Source_Labels.

Rendering only. All strings come from hargaikan.domain.messages.
"""

import streamlit as st

from hargaikan.domain import messages as m
from hargaikan.domain.models import SourceLabel
from hargaikan.ui.components import source_badge

st.set_page_config(page_title=m.APP_TITLE, page_icon="🐟", layout="centered")

st.title(m.APP_TITLE)
st.write(m.APP_INTRO)
st.write(m.APP_INTRO_SOURCES)

st.subheader(m.LEGEND_TITLE)
for label in SourceLabel:
    st.markdown(f"**{source_badge(label)}**: {m.SOURCE_DESCRIPTIONS[label.value]}")
