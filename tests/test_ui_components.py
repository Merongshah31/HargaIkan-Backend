"""Unit tests for hargaikan.ui.components and the stub pages."""

from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from hargaikan.domain import messages as m
from hargaikan.domain.models import SourceLabel
from hargaikan.ui.components import source_badge

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("label", list(SourceLabel), ids=lambda label: label.name)
def test_source_badge_has_icon_and_text(label: SourceLabel) -> None:
    badge = source_badge(label)
    assert badge == f"{m.SOURCE_ICONS[label.value]} {label.value}"
    assert m.DEMO_DATA not in badge


def test_source_badge_demo_suffix() -> None:
    badge = source_badge(SourceLabel.HARGA_SEJARAH, demo=True)
    assert badge.startswith(m.SOURCE_ICONS["Harga sejarah"])
    assert "Harga sejarah" in badge and badge.endswith(m.DEMO_DATA)


def test_every_label_has_icon_and_description() -> None:
    values = {label.value for label in SourceLabel}
    assert set(m.SOURCE_ICONS) == values and set(m.SOURCE_DESCRIPTIONS) == values
    assert all(m.SOURCE_ICONS.values()) and all(m.SOURCE_DESCRIPTIONS.values())


def _sections_script() -> None:
    import streamlit as st

    from hargaikan.ui.components import render_section

    def ok() -> None:
        st.write("isi baik")

    def broken() -> None:
        st.write("separuh")
        raise RuntimeError("boom")

    st.session_state["results"] = [
        render_section("Pertama", ok),
        render_section("Kedua", broken),
        render_section("Ketiga", ok),
    ]


def test_render_section_isolates_failures() -> None:
    at = AppTest.from_function(_sections_script).run()
    assert not at.exception
    assert at.session_state["results"] == [True, False, True]
    assert [s.value for s in at.subheader] == ["Pertama", "Kedua", "Ketiga"]
    assert [w.value for w in at.warning] == [m.SECTION_UNAVAILABLE]
    texts = [md.value for md in at.markdown]
    assert texts.count("isi baik") == 2
    assert "separuh" not in texts  # partial output of the failed section is replaced


@pytest.mark.parametrize(
    ("filename", "title"),
    [
        ("1_Dashboard.py", m.PAGE_TITLE_DASHBOARD),
        ("2_Cari_Ikan.py", m.PAGE_TITLE_CARI_IKAN),
        ("3_Profil.py", m.PAGE_TITLE_PROFIL),
        ("4_Ramalan.py", m.PAGE_TITLE_RAMALAN),
        ("5_Perjalanan.py", m.PAGE_TITLE_PERJALANAN),
    ],
)
def test_stub_pages_render_title(filename: str, title: str) -> None:
    at = AppTest.from_file(str(ROOT / "pages" / filename)).run()
    assert not at.exception
    assert [t.value for t in at.title] == [title]
