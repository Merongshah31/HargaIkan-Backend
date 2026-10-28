"""Property and unit tests for hargaikan.core.forecast_display (Properties 53–55)."""

from __future__ import annotations

from dataclasses import fields
from datetime import date, timedelta

from hypothesis import given
from hypothesis import strategies as st

from hargaikan.config import Settings
from hargaikan.core.forecast_display import ForecastView, forecast_view
from hargaikan.domain import messages as m
from hargaikan.domain.models import PriceBand, PricePrediction

S = Settings()
TODAY = date(2025, 6, 15)
prices = st.floats(min_value=1.0, max_value=100.0, allow_nan=False)
NUMERIC_FIELDS = ("predicted_median", "p10", "p90", "mae", "target_start", "target_end", "last_data_date",
                  "model_version", "method", "label", "p75_message", "early_buy_message")


@st.composite
def predictions(draw: st.DrawFn) -> PricePrediction:
    method = draw(st.sampled_from(["random_forest", "baseline_median4"]))
    if method == "random_forest" and draw(st.integers(min_value=0, max_value=4)):
        a, b = draw(prices), draw(prices)
        p10, p90 = min(a, b), max(a, b)
    else:
        p10 = p90 = None
    start = TODAY + timedelta(days=draw(st.integers(min_value=-14, max_value=14)))
    return PricePrediction(
        species_id="sp", location_id="kl", target_start=start, target_end=start + timedelta(days=6),
        predicted_median=draw(prices), p10=p10, p90=p90, method=method,  # type: ignore[arg-type]
        model_version=draw(st.sampled_from(["rf-20250601-abcd1234", "baseline-20250601-abcd1234"])),
        mae=draw(st.floats(min_value=0, max_value=10)),
        last_data_date=TODAY - timedelta(days=draw(st.integers(min_value=-2, max_value=40))),
        valid_until=TODAY + timedelta(days=draw(st.integers(min_value=-5, max_value=10))),
        publish_status=draw(st.sampled_from(["live", "live", "live", "staged", "retired"])),
    )


@st.composite
def bands(draw: st.DrawFn) -> PriceBand:
    a, b = draw(prices), draw(prices)
    p25, p75 = min(a, b), max(a, b)
    return PriceBand("sp", "kl", None, p25, (p25 + p75) / 2, p75, 5, "sederhana", date(2025, 1, 1), date(2025, 6, 1),
                     "IKANSIGHT")


def _visible_oracle(pred: PricePrediction | None, today: date) -> bool:
    return (pred is not None and pred.publish_status == "live" and today <= pred.valid_until
            and (today - pred.last_data_date).days <= S.forecast_max_data_age_days)


def _has_interval(pred: PricePrediction) -> bool:
    return pred.method != "baseline_median4" and pred.p10 is not None and pred.p90 is not None


# Feature: hargaikan-v2, Property 53: Only live, fresh forecasts are visible
@given(pred=st.none() | predictions(), band=st.none() | bands(), price=st.none() | prices,
       offset=st.integers(min_value=-3, max_value=3))
def test_only_live_fresh_forecasts_are_visible(pred, band, price, offset) -> None:  # type: ignore[no-untyped-def]
    """*For any* prediction (or None), band and today date, the forecast view is visible if and only if
    the prediction exists, is live, today ≤ valid_until and the last data date is within
    forecast_max_data_age_days. A visible view carries the label "Anggaran model", period, last data
    date, model version and MAE. A hidden view has no numeric values and the message "Ramalan belum
    tersedia."

    **Validates: Requirements 20.1, 20.2**
    """
    today = TODAY + timedelta(days=offset)
    v = forecast_view(pred, band, price, today, S)
    assert v.available == _visible_oracle(pred, today)
    if v.available:
        assert pred is not None
        assert v.label == "Anggaran model" and v.message is None
        assert (v.target_start, v.target_end) == (pred.target_start, pred.target_end)
        assert (v.last_data_date, v.model_version, v.mae) == (pred.last_data_date, pred.model_version, pred.mae)
        assert v.predicted_median == pred.predicted_median
        assert v.is_baseline == (pred.method == "baseline_median4")
        if _has_interval(pred):
            assert (v.p10, v.p90) == (pred.p10, pred.p90)
        else:
            assert v.p10 is None and v.p90 is None
    else:
        assert v.message == "Ramalan belum tersedia."
        for name in NUMERIC_FIELDS:
            assert getattr(v, name) is None, name
        assert v.may_exceed_p75 is False


# Feature: hargaikan-v2, Property 54: P75 message only when the interval supports it
@given(pred=predictions(), band=st.none() | bands(), price=st.none() | prices)
def test_p75_message_only_when_interval_supports_it(pred, band, price) -> None:  # type: ignore[no-untyped-def]
    """*For any* visible forecast and band, the P75 message is present if and only if the interval
    exists and P90 > band P75.

    **Validates: Requirements 20.3, 20.4**
    """
    v = forecast_view(pred, band, price, TODAY, S)
    if not v.available:
        return
    expected = _has_interval(pred) and band is not None and pred.p90 > band.p75
    assert v.may_exceed_p75 == expected
    assert v.p75_message == (
        "Julat anggaran menunjukkan harga mungkin melebihi P75 sejarah dalam tempoh ini." if expected else None)


# Feature: hargaikan-v2, Property 55: Early-buy only under both conditions
@given(pred=predictions(), band=st.none() | bands(), price=st.none() | prices)
def test_early_buy_only_under_both_conditions(pred, band, price) -> None:  # type: ignore[no-untyped-def]
    """*For any* visible forecast, band and current price, the early-buy text is present if and only if
    the interval exists, current price < P25 and P10 > current price. When present it contains the P10
    and P90 values, the uncertainty and not-an-instruction wording, and the storage reminder.

    **Validates: Requirements 20.5, 20.6**
    """
    v = forecast_view(pred, band, price, TODAY, S)
    if not v.available:
        return
    expected = (_has_interval(pred) and band is not None and price is not None
                and price < band.p25 and pred.p10 > price)
    assert (v.early_buy_message is not None) == expected
    if expected:
        text = v.early_buy_message
        assert f"RM{pred.p10:.2f}" in text and f"RM{pred.p90:.2f}" in text
        assert "tidak pasti" in text and "bukan arahan untuk membeli" in text
        assert "peti sejuk" in text and "masak dengan sempurna" in text
        assert text == m.EARLY_BUY.format(p10=f"{pred.p10:.2f}", p90=f"{pred.p90:.2f}")
        assert not any(p.lower() in text.lower() for p in m.FORBIDDEN_PHRASES)


# ------------------------------------------------------------------------------------ examples


def _pred(**kw) -> PricePrediction:  # type: ignore[no-untyped-def]
    base = dict(species_id="sp", location_id="kl", target_start=date(2025, 6, 16), target_end=date(2025, 6, 22),
                predicted_median=20.0, p10=18.0, p90=26.0, method="random_forest", model_version="rf-20250614-abcd1234",
                mae=1.2, last_data_date=date(2025, 6, 14), valid_until=date(2025, 6, 22), publish_status="live")
    base.update(kw)
    return PricePrediction(**base)  # type: ignore[arg-type]


BAND = PriceBand("sp", "kl", None, 19.0, 21.0, 24.0, 12, "tinggi", date(2025, 1, 1), date(2025, 6, 14), "IKANSIGHT")


def test_visible_forecast_with_both_messages() -> None:
    v = forecast_view(_pred(), BAND, 15.0, TODAY, S)
    assert v.available and v.label == "Anggaran model"
    assert v.may_exceed_p75 and v.p75_message == m.FORECAST_MAY_EXCEED_P75
    assert v.early_buy_message is not None and "RM18.00–RM26.00/kg" in v.early_buy_message


def test_hidden_cases() -> None:
    for pred, today in [
        (None, TODAY),
        (_pred(valid_until=date(2025, 6, 14)), TODAY),  # expired
        (_pred(last_data_date=TODAY - timedelta(days=22)), TODAY),  # stale
        (_pred(publish_status="staged"), TODAY),
    ]:
        assert forecast_view(pred, BAND, 15.0, today, S) == ForecastView(available=False, message=m.FORECAST_UNAVAILABLE)
    # Boundaries are inclusive: valid_until == today and age == max age stay visible.
    assert forecast_view(_pred(valid_until=TODAY, last_data_date=TODAY - timedelta(days=21)), BAND, None, TODAY, S).available


def test_baseline_never_shows_interval_messages() -> None:
    v = forecast_view(_pred(method="baseline_median4", p10=None, p90=None), BAND, 1.0, TODAY, S)
    assert v.available and v.is_baseline
    assert (v.p10, v.p90, v.p75_message, v.early_buy_message, v.may_exceed_p75) == (None, None, None, None, False)
    # Even a malformed baseline row carrying an interval shows no interval messages.
    v = forecast_view(_pred(method="baseline_median4"), BAND, 1.0, TODAY, S)
    assert (v.p10, v.p90, v.p75_message, v.early_buy_message) == (None, None, None, None)


def test_no_early_buy_when_price_not_below_p25_or_p10_not_above_price() -> None:
    assert forecast_view(_pred(), BAND, 19.0, TODAY, S).early_buy_message is None  # price == P25
    assert forecast_view(_pred(p10=15.0), BAND, 15.0, TODAY, S).early_buy_message is None  # P10 == price
    assert forecast_view(_pred(), None, 15.0, TODAY, S).early_buy_message is None
    assert forecast_view(_pred(p90=24.0), BAND, None, TODAY, S).p75_message is None  # P90 == P75


def test_forecast_view_fields_are_complete() -> None:
    names = {f.name for f in fields(ForecastView)}
    assert names == {"available", "message", "predicted_median", "p10", "p90", "method", "model_version", "mae",
                     "target_start", "target_end", "last_data_date", "label", "may_exceed_p75", "p75_message",
                     "early_buy_message", "is_baseline"}
