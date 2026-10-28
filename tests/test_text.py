"""Unit tests for hargaikan.core.text normalisation (including idempotence)."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from hargaikan.core.text import normalise_name, normalise_term


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("IKAN BAWAL HITAM", "IKAN BAWAL HITAM"),
        ("  ikan   bawal\thitam ", "IKAN BAWAL HITAM"),
        ("ikan\u00a0kembung", "IKAN KEMBUNG"),  # NFKC: no-break space → space
        ("ＩＫＡＮ　ＴＵＮＡ", "IKAN TUNA"),  # NFKC: full-width letters and ideographic space
        ("AEON ( AU2 )", "AEON ( AU2 )"),  # parentheses are left to normalise_premise
        ("", ""),
        ("   ", ""),
    ],
)
def test_normalise_name_examples(raw: str, expected: str) -> None:
    assert normalise_name(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("IKAN Bawal  HITAM", "ikan bawal hitam"),
        ("  Ikan\nKembung ", "ikan kembung"),
        ("ẞ", "ss"),  # capital sharp s: lower → "ß", whose upper is "SS"; the fixed point is "ss"
    ],
)
def test_normalise_term_examples(raw: str, expected: str) -> None:
    assert normalise_term(raw) == expected


@pytest.mark.parametrize("raw", ["ΐ", "ẞ", "ǅ", "ﬀ", "İstanbul", "a\u00b4", "ﾊﾟ", "Å", "\u0345"])
def test_tricky_unicode_is_idempotent(raw: str) -> None:
    once = normalise_name(raw)
    assert normalise_name(once) == once
    term = normalise_term(raw)
    assert normalise_term(term) == term


def test_whitespace_variants_give_the_same_term() -> None:
    assert normalise_term(" Ikan  BAWAL hitam") == normalise_term("IKAN BAWAL HITAM") == "ikan bawal hitam"


any_text = st.text(alphabet=st.characters(blacklist_categories=("Cs",)), max_size=40)


@given(any_text)
def test_normalise_name_is_idempotent(s: str) -> None:
    once = normalise_name(s)
    assert normalise_name(once) == once
    assert once == once.strip()
    assert "  " not in once


@given(any_text)
def test_normalise_term_is_idempotent(s: str) -> None:
    once = normalise_term(s)
    assert normalise_term(once) == once
