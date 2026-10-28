"""Cari Ikan page (stub): title only until the search and verdict tasks are implemented."""

import streamlit as st

from hargaikan.domain import messages as m

st.set_page_config(page_title=m.PAGE_TITLE_CARI_IKAN, layout="centered")
st.title(m.PAGE_TITLE_CARI_IKAN)
