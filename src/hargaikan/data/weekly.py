"""Weekly median series and catalog overview shared by the memory and Supabase price repositories.

Pure logic: no I/O and no supabase, pinecone, requests, streamlit or fastapi imports.

A record belongs to the week (Monday start) that contains its ``date_end``. Each week's value is
the median of ``price_rm_per_kg`` weighted by ``record_count`` (the same weighted quantile the
band builder uses), and its count is the sum of ``record_count``. For an ancestor location
(e.g. Kuala Lumpur) every record of the location itself and of all its descendants is used, as
for price bands.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from datetime import date, timedelta

from hargaikan.core.location_fallback import ancestors
from hargaikan.domain.models import Location
from hargaikan.pipeline.bands import weighted_quantile
from hargaikan.pipeline.synthetic import SYNTHETIC_SOURCE

WeeklyPoint = tuple[date, float, int]  # (week_start Monday, weighted median RM/kg, record_count)
WeeklyInput = tuple[date, float, int]  # (date_end, price_rm_per_kg, record_count)
PREMISE_LEVELS = ("pasar", "premis")


def catalog_overview(
    species_count: int, premise_count: int, period_ends: Iterable[date], band_sources: Iterable[str]
) -> dict:
    """The ``PriceRepository.catalog_overview`` dict shared by the memory and Supabase repositories.

    ``species_count`` (active species), ``premise_count`` (pasar + premis locations),
    ``latest_period`` (latest live band period_end, or None) and ``demo`` (True when any live
    band is SYNTHETIC, so the UI must show "Data demo").
    """
    return {
        "species_count": int(species_count),
        "premise_count": int(premise_count),
        "latest_period": max(period_ends, default=None),
        "demo": any(SYNTHETIC_SOURCE in s.split("+") for s in band_sources),
    }


def week_start(d: date) -> date:
    """Monday of the week containing ``d``."""
    return d - timedelta(days=d.weekday())


def subtree_ids(location_id: str, locations: Mapping[str, Location]) -> set[str]:
    """``location_id`` plus the ids of every location that has it as an ancestor.

    Uses the same ``ancestors`` walk as the band builder, so it terminates on bad data (cycles,
    missing parents). An id not in ``locations`` still contains itself, so records stored against
    it are not silently dropped.
    """
    out = {location_id}
    for loc_id in locations:
        if loc_id != location_id and any(a.id == location_id for a in ancestors(loc_id, locations)):
            out.add(loc_id)
    return out


def weekly_medians(points: Iterable[WeeklyInput]) -> list[WeeklyPoint]:
    """[(week_start, weighted median, Σ record_count)] sorted by week_start.

    Points with a non-positive price or a record_count below 1 are ignored (they cannot come
    from ok records). Weeks without points are not filled in.
    """
    groups: dict[date, tuple[list[float], list[int]]] = defaultdict(lambda: ([], []))
    for date_end, price, count in points:
        price, count = float(price), int(count)
        if price <= 0 or count < 1:
            continue
        values, weights = groups[week_start(date_end)]
        values.append(price)
        weights.append(count)
    return [
        (week, weighted_quantile(values, weights, 0.5), sum(weights))
        for week, (values, weights) in sorted(groups.items())
    ]
