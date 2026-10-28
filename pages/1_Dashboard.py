"""Dashboard page (stub): title only until the dashboard task is implemented."""

import streamlit as st

from hargaikan.domain import messages as m

st.set_page_config(page_title=m.PAGE_TITLE_DASHBOARD, layout="centered")
st.title(m.PAGE_TITLE_DASHBOARD)
