"""Seeded synthetic price records for development and test only (Requirement 8.1, 8.2).

Pure logic: no I/O and no supabase, pinecone, requests or streamlit imports. Every record carries
``source_name == SYNTHETIC_SOURCE``; the publisher refuses such data for production and the UI
labels it "Data demo".
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from datetime import date, timedelta

from hargaikan.domain.models import Location, PriceRecord, Species

SYNTHETIC_SOURCE = "SYNTHETIC"

# Shape of the demo data. These only drive the random generator; they are not reference prices.
_BASE_PRICE_RANGE = (8.0, 60.0)  # RM/kg, per species
_LOCATION_FACTOR_RANGE = (0.85, 1.20)  # per (species, location)
_WEEKLY_NOISE_SD = 0.05
_RECORD_COUNT_RANGE = (1, 12)
_MIN_PRICE = 1.0


def generate(
    species: Sequence[Species], premises: Sequence[Location], weeks: int, seed: int, start: date
) -> list[PriceRecord]:
    """One ok record per species × location × week, weeks starting at ``start`` (7-day periods).

    Deterministic for the same arguments (a private ``random.Random(seed)``; no global state).
    Every record has ``source_name == SYNTHETIC_SOURCE``, unit "kg", grade None, a price ≥ RM1
    rounded to 2 dp, ``price_low ≤ price ≤ price_high`` and record_count between 1 and 12.
    """
    rng = random.Random(seed)
    records: list[PriceRecord] = []
    for sp in species:
        base = rng.uniform(*_BASE_PRICE_RANGE)
        for loc in premises:
            factor = rng.uniform(*_LOCATION_FACTOR_RANGE)
            level = base * factor
            for w in range(max(0, weeks)):
                level *= 1.0 + rng.gauss(0.0, _WEEKLY_NOISE_SD)
                level = max(level, _MIN_PRICE)
                price = round(level, 2)
                date_start = start + timedelta(days=7 * w)
                records.append(
                    PriceRecord(
                        species_id=sp.id,
                        location_id=loc.id,
                        raw_item_name=sp.canonical_name_ms,
                        grade=None,
                        unit="kg",
                        price_rm_per_kg=price,
                        price_low=round(price * 0.9, 2),
                        price_high=round(price * 1.1, 2),
                        record_count=rng.randint(*_RECORD_COUNT_RANGE),
                        date_start=date_start,
                        date_end=date_start + timedelta(days=6),
                        source_name=SYNTHETIC_SOURCE,
                        source_snapshot=0,
                        quality_status="ok",
                    )
                )
    return records
