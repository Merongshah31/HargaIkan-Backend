"""Ramalan page (stub): title only until the forecast display task is implemented."""

import streamlit as st

from hargaikan.domain import messages as m

st.set_page_config(page_title=m.PAGE_TITLE_RAMALAN, layout="centered")
st.title(m.PAGE_TITLE_RAMALAN)
