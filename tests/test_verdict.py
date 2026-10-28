"""Unit tests for hargaikan.core.verdict and verdict_badge."""

from __future__ import annotations

from datetime import date

import pytest

from hargaikan.core.verdict import InvalidPriceError, Verdict, VerdictKind, compute_verdict, parse_price
from hargaikan.domain import messages as m
from hargaikan.domain.models import PriceBand
from hargaikan.ui.components import verdict_badge

BAND = PriceBand("s1", "kl", None, 15.0, 18.0, 20.0, 12, "sederhana", date(2024, 1, 1), date(2024, 1, 14), "IKANSIGHT")


@pytest.mark.parametrize(
    ("price", "kind", "text", "icon"),
    [
        (14.99, VerdictKind.BELOW, m.VERDICT_BELOW, "⬇️"),
        (15.0, VerdictKind.WITHIN, m.VERDICT_WITHIN, "✅"),
        (18.0, VerdictKind.WITHIN, m.VERDICT_WITHIN, "✅"),
        (20.0, VerdictKind.WITHIN, m.VERDICT_WITHIN, "✅"),
        (20.01, VerdictKind.ABOVE, m.VERDICT_ABOVE, "⬆️"),
    ],
)
def test_compute_verdict(price: float, kind: VerdictKind, text: str, icon: str) -> None:
    assert compute_verdict(price, BAND) == Verdict(kind, text, icon)


def test_no_band() -> None:
    assert compute_verdict(18.0, None) == Verdict(VerdictKind.NO_DATA, m.NO_DATA, "ℹ️")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(18, 18.0), ("18", 18.0), ("RM18.50", 18.5), ("1,200.00", 1200.0), (" rm 7 ", 7.0), (2.5, 2.5)],
)
def test_parse_price_valid(raw: object, expected: float) -> None:
    assert parse_price(raw) == expected


@pytest.mark.parametrize(
    "raw", [None, True, False, "", "abc", "RM-3", "0", 0, -1, float("nan"), float("inf"), "nan", "-inf"]
)
def test_parse_price_invalid(raw: object) -> None:
    with pytest.raises(InvalidPriceError):
        parse_price(raw)


def test_invalid_price_message() -> None:
    with pytest.raises(InvalidPriceError, match="Sila masukkan harga yang sah melebihi RM0."):
        parse_price("abc")


def test_verdict_badge() -> None:
    assert verdict_badge(compute_verdict(18.0, BAND)) == "✅ Dalam julat sejarah"
    assert verdict_badge(compute_verdict(1.0, None)) == "ℹ️ Data belum tersedia"
