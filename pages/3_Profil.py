"""Profil page (stub): title only until the profile task is implemented."""

import streamlit as st

from hargaikan.domain import messages as m

st.set_page_config(page_title=m.PAGE_TITLE_PROFIL, layout="centered")
st.title(m.PAGE_TITLE_PROFIL)
