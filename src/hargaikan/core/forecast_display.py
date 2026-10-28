"""Forecast display rules: visibility, the P75 message and the early-buy suggestion (Requirement 20).

Pure logic (layer 1): no I/O and no supabase, pinecone, requests, streamlit or fastapi imports.
Every number in a visible view comes from a published prediction (label "Anggaran model"); a
hidden view carries no numbers, only FORECAST_UNAVAILABLE.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from hargaikan.config import Settings
from hargaikan.domain import messages as m
from hargaikan.domain.models import PriceBand, PricePrediction, SourceLabel

BASELINE_METHOD = "baseline_median4"


@dataclass(frozen=True)
class ForecastView:
    available: bool
    message: str | None = None  # FORECAST_UNAVAILABLE when not available
    predicted_median: float | None = None
    p10: float | None = None  # None for baseline predictions (no interval)
    p90: float | None = None
    method: str | None = None
    model_version: str | None = None
    mae: float | None = None
    target_start: date | None = None
    target_end: date | None = None
    last_data_date: date | None = None
    label: str | None = None  # "Anggaran model" when available
    may_exceed_p75: bool = False
    p75_message: str | None = None  # FORECAST_MAY_EXCEED_P75 when may_exceed_p75
    early_buy_message: str | None = None  # EARLY_BUY filled with 2-dp P10/P90
    is_baseline: bool = False


UNAVAILABLE = ForecastView(available=False, message=m.FORECAST_UNAVAILABLE)


def is_visible(prediction: PricePrediction | None, today: date, s: Settings) -> bool:
    """Live, today ≤ valid_until and last data no older than forecast_max_data_age_days."""
    return (
        prediction is not None
        and prediction.publish_status == "live"
        and today <= prediction.valid_until
        and (today - prediction.last_data_date).days <= s.forecast_max_data_age_days
    )


def forecast_view(
    prediction: PricePrediction | None,
    band: PriceBand | None,
    current_price: float | None,
    today: date,
    s: Settings,
) -> ForecastView:
    """Build the forecast view for one species and location.

    Hidden (``UNAVAILABLE``) when the prediction is missing, not live, past valid_until or stale.
    The interval exists only when both P10 and P90 are present and the method is not the baseline.
    P75 message ⇔ interval and band and P90 > band P75. Early-buy ⇔ interval and band and
    current_price < band P25 and P10 > current_price. ``current_price`` is the user's quote.
    """
    if not is_visible(prediction, today, s):
        return UNAVAILABLE
    assert prediction is not None
    is_baseline = prediction.method == BASELINE_METHOD
    has_iv = not is_baseline and prediction.p10 is not None and prediction.p90 is not None
    p10 = prediction.p10 if has_iv else None
    p90 = prediction.p90 if has_iv else None

    may_exceed = has_iv and band is not None and p90 > band.p75  # type: ignore[operator]
    early = (
        has_iv
        and band is not None
        and current_price is not None
        and current_price < band.p25
        and p10 > current_price  # type: ignore[operator]
    )
    return ForecastView(
        available=True,
        message=None,
        predicted_median=prediction.predicted_median,
        p10=p10,
        p90=p90,
        method=prediction.method,
        model_version=prediction.model_version,
        mae=prediction.mae,
        target_start=prediction.target_start,
        target_end=prediction.target_end,
        last_data_date=prediction.last_data_date,
        label=SourceLabel.ANGGARAN_MODEL.value,
        may_exceed_p75=may_exceed,
        p75_message=m.FORECAST_MAY_EXCEED_P75 if may_exceed else None,
        early_buy_message=m.EARLY_BUY.format(p10=f"{p10:.2f}", p90=f"{p90:.2f}") if early else None,
        is_baseline=is_baseline,
    )
