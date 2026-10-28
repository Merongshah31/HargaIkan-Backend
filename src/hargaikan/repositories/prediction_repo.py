"""Supabase :class:`~hargaikan.repositories.protocols.PredictionRepository` (anon key, live rows only)."""

from __future__ import annotations

from typing import Any

from hargaikan.domain.models import PricePrediction
from hargaikan.repositories.rows import (
    LIVE,
    METRIC_COLUMNS,
    PREDICTION_COLUMNS,
    fetch_all,
    metric_from_row,
    prediction_from_row,
)


class SupabasePredictionRepository:
    def __init__(self, client: Any) -> None:
        self.client = client

    def live_prediction(self, species_id: str, location_id: str) -> PricePrediction | None:
        """The live prediction with the latest target_start, or None. Freshness (valid_until) is
        checked by the forecast service, not here."""
        resp = (
            self.client.table("price_predictions")
            .select(PREDICTION_COLUMNS)
            .eq("species_id", str(species_id))
            .eq("location_id", str(location_id))
            .eq("publish_status", LIVE)
            .order("target_start", desc=True)
            .limit(1)
            .execute()
        )
        rows = resp.data or []
        return prediction_from_row(rows[0]) if rows else None

    def metrics_for(self, model_version: str) -> list[dict]:
        rows = fetch_all(
            lambda: self.client.table("model_metrics")
            .select(METRIC_COLUMNS)
            .eq("model_version", model_version)
            .eq("publish_status", LIVE)
            .order("id")
        )
        return [metric_from_row(r) for r in rows]
