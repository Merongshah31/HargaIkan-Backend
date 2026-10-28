"""Property tests for hargaikan.core.verdict (Properties 25–27)."""

from __future__ import annotations

import math
from datetime import date
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from hargaikan.core.verdict import InvalidPriceError, VerdictKind, compute_verdict, parse_price
from hargaikan.domain import messages as m
from hargaikan.domain.models import PriceBand

positive = st.floats(min_value=0.01, max_value=1e6, allow_nan=False, allow_infinity=False)
dates = st.dates(min_value=date(2020, 1, 1), max_value=date(2030, 12, 31))


@st.composite
def p25_p75(draw: st.DrawFn) -> tuple[float, float]:
    a, b = draw(positive), draw(positive)
    return min(a, b), max(a, b)


@st.composite
def bands_with(draw: st.DrawFn, p25: float, p75: float) -> PriceBand:
    start = draw(dates)
    return PriceBand(
        species_id=draw(st.text(min_size=1, max_size=8)),
        location_id=draw(st.text(min_size=1, max_size=8)),
        grade=draw(st.none() | st.sampled_from(["A", "B", "C"])),
        p25=p25,
        median=draw(st.floats(min_value=p25, max_value=p75)),
        p75=p75,
        record_count=draw(st.integers(min_value=1, max_value=10_000)),
        confidence_level=draw(st.sampled_from(["rendah", "sederhana", "tinggi"])),
        period_start=start,
        period_end=draw(st.dates(min_value=start, max_value=date(2031, 12, 31))),
        band_source=draw(st.sampled_from(["IKANSIGHT", "SYNTHETIC"])),
    )


@st.composite
def band_and_price(draw: st.DrawFn) -> tuple[PriceBand, float]:
    p25, p75 = draw(p25_p75())
    band = draw(bands_with(p25, p75))
    # Mix arbitrary prices with exact bounds so the inclusive edges are exercised.
    price = draw(positive | st.sampled_from([p25, p75]))
    return band, price


def _no_forbidden(text: str) -> bool:
    lowered = text.lower()
    return not any(p.lower() in lowered for p in m.FORBIDDEN_PHRASES)


# Feature: hargaikan-v2, Property 25: Verdict partitions the price line
@given(bp=band_and_price(), no_band=st.booleans())
def test_verdict_partitions_price_line(bp: tuple[PriceBand, float], no_band: bool) -> None:
    """*For any* band with 0 < P25 ≤ P75 and any valid price, the verdict is "Di bawah julat sejarah"
    when price < P25, "Dalam julat sejarah" when P25 ≤ price ≤ P75 and "Di atas julat sejarah" when
    price > P75. With no band it is "Data belum tersedia". Every verdict has a non-empty icon and text
    containing none of the Forbidden_Phrases.

    **Validates: Requirements 10.1, 10.2, 10.3, 10.5, 10.6, 10.8, 24.1**
    """
    band, price = bp
    if no_band:
        v = compute_verdict(price, None)
        assert (v.kind, v.text) == (VerdictKind.NO_DATA, "Data belum tersedia")
    else:
        v = compute_verdict(price, band)
        if price < band.p25:
            assert (v.kind, v.text) == (VerdictKind.BELOW, "Di bawah julat sejarah")
        elif price <= band.p75:
            assert (v.kind, v.text) == (VerdictKind.WITHIN, "Dalam julat sejarah")
        else:
            assert (v.kind, v.text) == (VerdictKind.ABOVE, "Di atas julat sejarah")
    assert v.icon and v.text
    assert _no_forbidden(v.text)


@st.composite
def two_bands_same_bounds(draw: st.DrawFn) -> tuple[PriceBand, PriceBand]:
    p25, p75 = draw(p25_p75())
    return draw(bands_with(p25, p75)), draw(bands_with(p25, p75))


# Feature: hargaikan-v2, Property 26: Verdict depends only on price, P25 and P75
@given(bands=two_bands_same_bounds(), price=positive)
def test_verdict_depends_only_on_price_p25_p75(bands: tuple[PriceBand, PriceBand], price: float) -> None:
    """*For any* price and two bands with equal P25 and P75 but any differing median, record_count,
    confidence, period or source, the two verdicts are equal.

    **Validates: Requirements 10.7**
    """
    a, b = bands
    assert compute_verdict(price, a) == compute_verdict(price, b)


non_positive_numbers = (
    st.integers(max_value=0)
    | st.floats(max_value=0, allow_nan=False)
    | st.decimals(max_value=0, allow_nan=False, allow_infinity=False)
)
invalid_inputs = st.one_of(
    st.none(),
    st.booleans(),
    st.just(math.nan),
    st.sampled_from([math.inf, -math.inf, Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")]),
    non_positive_numbers,
    non_positive_numbers.map(str),
    st.integers(min_value=0, max_value=10_000).map(lambda n: f"RM-{n}"),
    st.sampled_from(["", " ", "RM", "abc", "RM abc", "nan", "inf", "-inf", "RM-3", "0", "RM0.00", "dua"]),
    st.text(alphabet="abcdxyzRMPQ !?", max_size=10),
    st.lists(st.integers(min_value=1), max_size=2),
)

cents = st.integers(min_value=1, max_value=10**9)


@st.composite
def formatted_valid(draw: st.DrawFn) -> tuple[object, float]:
    n = draw(cents)
    value = n / 100
    fmt = draw(st.sampled_from(["{v:.2f}", "{v:,.2f}", "RM{v:.2f}", "RM{v:,.2f}", "rm {v:,.2f}", " RM{v:,.2f} "]))
    return fmt.format(v=value), value


# Feature: hargaikan-v2, Property 27: Invalid prices are rejected
@given(raw=invalid_inputs)
def test_invalid_prices_are_rejected(raw: object) -> None:
    """*For any* input that is non-numeric, boolean, NaN, infinite, zero or negative (including strings
    such as "RM-3"), `parse_price` raises InvalidPriceError. *For any* finite positive number, optionally
    formatted with "RM" and thousands separators, it returns that number.

    **Validates: Requirements 10.4**
    """
    with pytest.raises(InvalidPriceError):
        parse_price(raw)


# Feature: hargaikan-v2, Property 27: Invalid prices are rejected
@given(
    case=formatted_valid()
    | positive.map(lambda x: (x, x))
    | st.integers(min_value=1, max_value=10**9).map(lambda n: (n, float(n)))
)
def test_valid_prices_parse_to_their_number(case: tuple[object, float]) -> None:
    """*For any* input that is non-numeric, boolean, NaN, infinite, zero or negative (including strings
    such as "RM-3"), `parse_price` raises InvalidPriceError. *For any* finite positive number, optionally
    formatted with "RM" and thousands separators, it returns that number.

    **Validates: Requirements 10.4**
    """
    raw, expected = case
    assert parse_price(raw) == expected
