"""Supabase :class:`~hargaikan.repositories.protocols.PriceRepository` (anon key, live rows only).

Every fact query filters ``publish_status = 'live'`` explicitly, in addition to the RLS policy
that hides staged and retired rows from anon and authenticated users.
"""

from __future__ import annotations

from typing import Any

from hargaikan.data.weekly import PREMISE_LEVELS, WeeklyPoint, catalog_overview, subtree_ids, weekly_medians
from hargaikan.domain.models import PriceBand
from hargaikan.pipeline.synthetic import SYNTHETIC_SOURCE
from hargaikan.repositories.rows import (
    BAND_COLUMNS,
    LIVE,
    LOCATION_COLUMNS,
    band_from_row,
    fetch_all,
    location_from_row,
    to_date,
)

SERIES_COLUMNS = "location_id, price_rm_per_kg, record_count, date_end"


class SupabasePriceRepository:
    def __init__(self, client: Any) -> None:
        self.client = client

    def bands_for_species(self, species_id: str) -> list[PriceBand]:
        rows = fetch_all(
            lambda: self.client.table("price_bands")
            .select(BAND_COLUMNS)
            .eq("species_id", str(species_id))
            .eq("publish_status", LIVE)
            .order("id")
        )
        return [band_from_row(r) for r in rows]

    def _locations(self) -> dict:
        rows = fetch_all(lambda: self.client.table("locations").select(LOCATION_COLUMNS).order("id"))
        return {loc.id: loc for loc in map(location_from_row, rows)}

    def weekly_series(self, species_id: str, location_id: str) -> list[WeeklyPoint]:
        """Weekly weighted medians of live ok price_records at the location and its descendants.

        A location with no descendants is filtered in the query; for an ancestor (e.g. Kuala
        Lumpur) the species' live ok records are read and filtered by the location subtree here,
        which avoids a very long ``in.(…)`` URL.
        """
        ids = subtree_ids(str(location_id), self._locations())

        def build() -> Any:
            q = (
                self.client.table("price_records")
                .select(SERIES_COLUMNS)
                .eq("species_id", str(species_id))
                .eq("publish_status", LIVE)
                .eq("quality_status", "ok")
            )
            if len(ids) == 1:
                q = q.eq("location_id", str(location_id))
            return q.order("id")

        rows = fetch_all(build)
        return weekly_medians(
            (to_date(r["date_end"]), float(r["price_rm_per_kg"]), int(r["record_count"]))  # type: ignore[misc]
            for r in rows
            if str(r["location_id"]) in ids
        )

    def catalog_overview(self) -> dict:
        from postgrest.types import CountMethod

        species = (
            self.client.table("species").select("id", count=CountMethod.exact, head=True).eq("active", True).execute()
        )
        premises = (
            self.client.table("locations")
            .select("id", count=CountMethod.exact, head=True)
            .in_("level", list(PREMISE_LEVELS))
            .execute()
        )
        latest = (
            self.client.table("price_bands")
            .select("period_end")
            .eq("publish_status", LIVE)
            .order("period_end", desc=True)
            .limit(1)
            .execute()
        )
        synthetic = (
            self.client.table("price_bands")
            .select("id")
            .eq("publish_status", LIVE)
            .like("band_source", f"%{SYNTHETIC_SOURCE}%")
            .limit(1)
            .execute()
        )
        return catalog_overview(
            int(species.count or 0),
            int(premises.count or 0),
            [to_date(r["period_end"]) for r in (latest.data or [])],
            [SYNTHETIC_SOURCE] if synthetic.data else [],
        )
