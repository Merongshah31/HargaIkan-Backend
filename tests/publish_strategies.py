"""Hypothesis strategies shared by the band, synthetic and publish property tests."""

from __future__ import annotations

import dataclasses
import math
from collections.abc import Mapping, Sequence
from datetime import date, timedelta

from hypothesis import strategies as st

from hargaikan.config import Settings
from hargaikan.domain.ids import species_id
from hargaikan.domain.models import (
    FishProfile,
    Location,
    PricePrediction,
    PriceRecord,
    SimilarityEdge,
    Species,
    SpeciesAlias,
)
from hargaikan.pipeline.bands import build_bands
from hargaikan.pipeline.cleaning import normalise_premise
from hargaikan.pipeline.locations import build_hierarchy, by_id
from hargaikan.pipeline.publish import Batch, make_batch
from hargaikan.pipeline.synthetic import SYNTHETIC_SOURCE

SETTINGS = Settings()
NON_OK_STATUSES = ("invalid_price", "invalid_count", "invalid_date", "inconsistent_range", "outlier")
REAL_SOURCE = "IKANSIGHT"

prices = st.integers(min_value=100, max_value=50_000).map(lambda c: c / 100)  # RM1.00 – RM500.00
counts = st.integers(min_value=1, max_value=30)
dates = st.dates(min_value=date(2025, 1, 1), max_value=date(2027, 12, 31))
grades = st.sampled_from([None, "A", "B"])


@st.composite
def hierarchies(draw: st.DrawFn, min_size: int = 1, max_size: int = 8) -> dict[str, Location]:
    """A random acyclic tree: loc-0 is the root (nasional); loc-i's parent is some loc-j, j < i."""
    n = draw(st.integers(min_value=min_size, max_value=max_size))
    out: dict[str, Location] = {}
    for i in range(n):
        parent = None if i == 0 else f"loc-{draw(st.integers(min_value=0, max_value=i - 1))}"
        level = "nasional" if i == 0 else draw(st.sampled_from(["negeri", "kawasan", "pasar", "premis"]))
        out[f"loc-{i}"] = Location(f"loc-{i}", f"Lokasi {i}", level, parent, None, None)  # type: ignore[arg-type]
    return out


@st.composite
def price_records(
    draw: st.DrawFn,
    location_ids: Sequence[str],
    species_ids: Sequence[str] = ("sp-a", "sp-b"),
    sources: Sequence[str] = (REAL_SOURCE,),
    statuses: Sequence[str] = ("ok",),
    grade_strategy: st.SearchStrategy = grades,
) -> PriceRecord:
    start = draw(dates)
    return PriceRecord(
        species_id=draw(st.sampled_from(list(species_ids))),
        location_id=draw(st.sampled_from(list(location_ids))),
        raw_item_name=draw(st.sampled_from(["IKAN A", "IKAN B (A)", "IKAN C"])),
        grade=draw(grade_strategy),
        unit="kg",
        price_rm_per_kg=draw(prices),
        price_low=None,
        price_high=None,
        record_count=draw(counts),
        date_start=start,
        date_end=start + timedelta(days=draw(st.integers(min_value=0, max_value=13))),
        source_name=draw(st.sampled_from(list(sources))),
        source_snapshot=draw(st.integers(min_value=0, max_value=3)),
        quality_status=draw(st.sampled_from(list(statuses))),  # type: ignore[arg-type]
    )


def chain(location_id: str, locations: Mapping[str, Location]) -> list[str]:
    """Oracle: the location and its ancestors (independent of core.location_fallback)."""
    out, cur = [], location_id
    while cur is not None and cur in locations and cur not in out:
        out.append(cur)
        cur = locations[cur].parent_id
    return out


def oracle_quantile(records: Sequence[PriceRecord], q: float) -> float:
    """Unweighted inverse-CDF quantile of the multiset with each price repeated record_count times."""
    expanded = sorted(p for r in records for p in [r.price_rm_per_kg] * r.record_count)
    k = max(math.ceil(q * len(expanded)) - 1, 0)
    return expanded[k]


# --- Batches -------------------------------------------------------------------------------------


@st.composite
def valid_batches(draw: st.DrawFn, source: str | None = None) -> Batch:
    """A valid "prices" Batch (optionally with predictions, metrics, profiles and similarity).

    ``source``: REAL_SOURCE or SYNTHETIC_SOURCE for every record; None draws one of them.
    """
    tag = draw(st.sampled_from(["X", "Y", "Z"]))
    n_species = draw(st.integers(min_value=1, max_value=3))
    species = [
        Species(species_id(f"SP_{tag}_{i}"), f"SP_{tag}_{i}", f"IKAN {tag} {i}", None, (), True) for i in range(n_species)
    ]
    aliases = [SpeciesAlias(s.id, s.canonical_name_ms, "raw") for s in species]
    branches = draw(st.lists(st.sampled_from(["AU2", "KLCC", "TTDI", "MID VALLEY"]), min_size=1, max_size=3, unique=True))
    premises = [normalise_premise(f"AEON ({b})") for b in branches]
    locations = build_hierarchy(premises, {})
    premis_ids = [loc.id for loc in locations if loc.level == "premis"]
    src = source or draw(st.sampled_from([REAL_SOURCE, SYNTHETIC_SOURCE]))
    records = draw(
        st.lists(
            price_records(premis_ids, [s.id for s in species], sources=[src], statuses=["ok", "outlier"]),
            min_size=1,
            max_size=8,
        )
    )
    records[0] = dataclasses.replace(records[0], quality_status="ok")  # at least one band
    # Unique live key per batch (source_name, location_id, raw_item_name, date_start, date_end).
    seen: dict[tuple, PriceRecord] = {}
    for r in records:
        seen.setdefault((r.source_name, r.location_id, r.raw_item_name, r.date_start, r.date_end), r)
    records = list(seen.values())
    bands = build_bands(records, by_id(locations), SETTINGS)
    predictions = []
    for _ in range(draw(st.integers(min_value=0, max_value=2))):
        start = draw(dates)
        median = draw(prices)
        has_iv = draw(st.booleans())
        predictions.append(
            PricePrediction(
                species_id=species[0].id, location_id=premis_ids[0], target_start=start,
                target_end=start + timedelta(days=6), predicted_median=median,
                p10=median * 0.9 if has_iv else None, p90=median * 1.1 if has_iv else None,
                method="random_forest" if has_iv else "baseline_median4", model_version="rf-test", mae=1.0,
                last_data_date=start - timedelta(days=1), valid_until=start + timedelta(days=6), publish_status="staged",
            )
        )
    metrics = (
        [{"model_version": "rf-test", "scope": "overall", "mae": 1.0, "decision": "model"}] if draw(st.booleans()) else []
    )
    profiles = (
        [FishProfile(species[0].code, "lembut", "manis", "sederhana", ("goreng",), (), None)] if draw(st.booleans()) else []
    )
    similarity = (
        [SimilarityEdge(species[0].id, species[1].id, draw(st.floats(0, 1)), "Sama", ("tekstur",), False)]
        if len(species) > 1 and draw(st.booleans())
        else []
    )
    return make_batch(
        "prices", species=species, aliases=aliases, locations=locations, records=records, bands=bands,
        predictions=predictions, metrics=metrics, profiles=profiles, similarity=similarity,
    )


DEFECTS = ("null_field", "bad_price", "bad_band", "bad_count", "p10_gt_p90", "unknown_ref", "no_fact_rows")


@st.composite
def with_defect(draw: st.DrawFn, batch: Batch, defect: str) -> Batch:
    """``batch`` with one injected defect of the given class (Requirement 7.3)."""
    rep = dataclasses.replace
    if defect == "null_field":
        attr, fld = draw(
            st.sampled_from(
                [("records", "raw_item_name"), ("records", "date_start"), ("bands", "confidence_level"),
                 ("bands", "period_end"), ("species", "code"), ("locations", "name"), ("aliases", "alias")]
            )
        )
        rows = list(getattr(batch, attr))
        i = draw(st.integers(min_value=0, max_value=len(rows) - 1))
        rows[i] = rep(rows[i], **{fld: None})
        return rep(batch, **{attr: rows})
    if defect == "bad_price":
        rows = list(batch.records)
        i = draw(st.integers(min_value=0, max_value=len(rows) - 1))
        rows[i] = rep(rows[i], price_rm_per_kg=draw(st.sampled_from([0.0, -0.01, -5.0])))
        return rep(batch, records=rows)
    if defect == "bad_band":
        rows = list(batch.bands)
        i = draw(st.integers(min_value=0, max_value=len(rows) - 1))
        b = rows[i]
        delta = draw(st.floats(min_value=0.01, max_value=100))
        kind = draw(st.sampled_from(["p25_above_median", "p75_below_median", "p25_not_positive"]))
        if kind == "p25_above_median":
            rows[i] = rep(b, p25=b.median + delta)
        elif kind == "p75_below_median":
            rows[i] = rep(b, p75=b.median - delta)
        else:
            rows[i] = rep(b, p25=-delta, median=max(b.median, 0.0))
        return rep(batch, bands=rows)
    if defect == "bad_count":
        attr = draw(st.sampled_from(["records", "bands"]))
        rows = list(getattr(batch, attr))
        i = draw(st.integers(min_value=0, max_value=len(rows) - 1))
        rows[i] = rep(rows[i], record_count=draw(st.integers(min_value=-5, max_value=0)))
        return rep(batch, **{attr: rows})
    if defect == "p10_gt_p90":
        base = batch.predictions[0] if batch.predictions else None
        start = date(2026, 1, 5)
        p90 = draw(st.floats(min_value=1, max_value=100))
        bad = PricePrediction(
            species_id=batch.species[0].id, location_id=batch.records[0].location_id, target_start=start,
            target_end=start + timedelta(days=6), predicted_median=p90, p10=p90 + draw(st.floats(0.01, 50)), p90=p90,
            method="random_forest", model_version="rf-test", mae=1.0, last_data_date=start,
            valid_until=start + timedelta(days=6), publish_status="staged",
        ) if base is None else rep(base, p10=(base.p90 or 10.0) + 1.0, p90=base.p90 or 10.0)
        return rep(batch, predictions=[bad, *batch.predictions[1:]])
    if defect == "unknown_ref":
        attr, fld = draw(
            st.sampled_from([("records", "species_id"), ("records", "location_id"), ("bands", "species_id"),
                             ("bands", "location_id"), ("aliases", "species_id")])
        )
        rows = list(getattr(batch, attr))
        i = draw(st.integers(min_value=0, max_value=len(rows) - 1))
        rows[i] = rep(rows[i], **{fld: str(draw(st.uuids()))})
        return rep(batch, **{attr: rows})
    if defect == "no_fact_rows":
        return rep(batch, records=[], bands=[], predictions=[], metrics=[])
    raise ValueError(defect)
