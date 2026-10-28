"""Property tests for hargaikan.pipeline.bands (Properties 10, 12, 13, 14 and 15 of the design)."""

from __future__ import annotations

import dataclasses
from collections import defaultdict

import pytest
from hypothesis import given
from hypothesis import strategies as st

from hargaikan.config import Settings
from hargaikan.domain.models import Location
from hargaikan.pipeline.bands import MixedSourceError, build_bands, confidence_level
from hargaikan.pipeline.synthetic import SYNTHETIC_SOURCE
from tests.publish_strategies import (
    NON_OK_STATUSES,
    REAL_SOURCE,
    SETTINGS,
    chain,
    hierarchies,
    oracle_quantile,
    price_records,
)

ROOT = {"loc-0": Location("loc-0", "Malaysia", "nasional", None, None, None)}
RANK = {"rendah": 0, "sederhana": 1, "tinggi": 2}


# Feature: hargaikan-v2, Property 12: Band quantile invariants
@given(
    data=st.data(),
    records=st.lists(
        price_records(["loc-0"], species_ids=["sp-a"], grade_strategy=st.none()), min_size=1, max_size=20
    ),
)
def test_band_quantile_invariants(data: st.DataObject, records: list) -> None:
    """**Validates: Requirements 5.1, 5.2**

    For any non-empty set of ok records for one band group, min ≤ P25 ≤ median ≤ P75 ≤ max, each
    quantile equals the unweighted quantile of the multiset with each price repeated
    bilangan_rekod times, record_count is the weight sum, the period spans the earliest start to
    the latest end, and the result does not depend on record order.
    """
    bands = build_bands(records, ROOT, SETTINGS)
    assert len(bands) == 1
    b = bands[0]
    values = [r.price_rm_per_kg for r in records]
    assert min(values) <= b.p25 <= b.median <= b.p75 <= max(values)
    assert b.p25 == oracle_quantile(records, 0.25)
    assert b.median == oracle_quantile(records, 0.5)
    assert b.p75 == oracle_quantile(records, 0.75)
    assert b.record_count == sum(r.record_count for r in records)
    assert b.period_start == min(r.date_start for r in records)
    assert b.period_end == max(r.date_end for r in records)
    assert b.band_source == REAL_SOURCE
    assert (b.species_id, b.location_id, b.grade) == ("sp-a", "loc-0", None)
    shuffled = data.draw(st.permutations(records))
    assert build_bands(shuffled, ROOT, SETTINGS) == bands


# Feature: hargaikan-v2, Property 13: Confidence level thresholds
@given(n1=st.integers(min_value=1, max_value=10**6), n2=st.integers(min_value=1, max_value=10**6),
       lo=st.integers(min_value=1, max_value=50), gap=st.integers(min_value=0, max_value=50))
def test_confidence_level_thresholds(n1: int, n2: int, lo: int, gap: int) -> None:
    """**Validates: Requirements 5.3**

    For any record_count n ≥ 1, confidence_level(n) is rendah for n < 3, sederhana for
    3 ≤ n < 10 and tinggi for n ≥ 10 (default thresholds; configurable thresholds behave the
    same way), and the level never decreases as n grows.
    """
    expected = "rendah" if n1 < 3 else "sederhana" if n1 < 10 else "tinggi"
    assert confidence_level(n1, SETTINGS) == expected
    s = Settings(confidence_sederhana_min=lo, confidence_tinggi_min=lo + gap)
    custom = "rendah" if n1 < lo else "sederhana" if n1 < lo + gap else "tinggi"
    assert confidence_level(n1, s) == custom
    small, big = sorted((n1, n2))
    for settings in (SETTINGS, s):
        assert RANK[confidence_level(small, settings)] <= RANK[confidence_level(big, settings)]


# Feature: hargaikan-v2, Property 14: Hierarchy aggregation
@given(data=st.data(), locations=hierarchies())
def test_hierarchy_aggregation(data: st.DataObject, locations: dict) -> None:
    """**Validates: Requirements 5.4, 5.5**

    For any acyclic hierarchy and ok records, the band keys are exactly
    {(species, location, grade or None)} over each record's location and every ancestor, and each
    band is computed from exactly the ok records at that location's descendants (or itself).
    """
    records = data.draw(st.lists(price_records(list(locations)), min_size=0, max_size=15))
    bands = build_bands(records, locations, SETTINGS)

    expected_keys = set()
    for r in records:
        for loc in chain(r.location_id, locations):
            expected_keys.add((r.species_id, loc, None))
            if r.grade:
                expected_keys.add((r.species_id, loc, r.grade))
    keys = [(b.species_id, b.location_id, b.grade) for b in bands]
    assert len(keys) == len(set(keys))
    assert set(keys) == expected_keys

    for b in bands:
        contributing = [
            r for r in records
            if r.species_id == b.species_id
            and b.location_id in chain(r.location_id, locations)
            and (b.grade is None or r.grade == b.grade)
        ]
        assert contributing
        assert b.record_count == sum(r.record_count for r in contributing)
        assert b.p25 == oracle_quantile(contributing, 0.25)
        assert b.median == oracle_quantile(contributing, 0.5)
        assert b.p75 == oracle_quantile(contributing, 0.75)
        assert b.period_start == min(r.date_start for r in contributing)
        assert b.period_end == max(r.date_end for r in contributing)


def _groups_mix(records: list, locations: dict) -> bool:
    sources: dict[tuple, set] = defaultdict(set)
    for r in records:
        if r.quality_status != "ok":
            continue
        for loc in chain(r.location_id, locations):
            sources[(r.species_id, loc, None)].add(r.source_name)
            if r.grade:
                sources[(r.species_id, loc, r.grade)].add(r.source_name)
    return any(SYNTHETIC_SOURCE in s and len(s) > 1 for s in sources.values())


# Feature: hargaikan-v2, Property 15: Mixed synthetic and real sources are rejected
@given(data=st.data(), locations=hierarchies(), force_mix=st.booleans())
def test_mixed_sources_are_rejected(data: st.DataObject, locations: dict, force_mix: bool) -> None:
    """**Validates: Requirements 5.6**

    For any band group containing at least one SYNTHETIC and at least one non-SYNTHETIC record,
    build_bands raises MixedSourceError and returns no bands; without such a group it never raises.
    """
    ids = list(locations)
    records = data.draw(
        st.lists(price_records(ids, sources=[REAL_SOURCE, SYNTHETIC_SOURCE]), min_size=0, max_size=12)
    )
    if force_mix:  # same species, any two locations: they always share the root's all-grade group
        records += [
            data.draw(price_records(ids, species_ids=["sp-a"], sources=[SYNTHETIC_SOURCE])),
            data.draw(price_records(ids, species_ids=["sp-a"], sources=["IKANSIGHT", "MANUAL"])),
        ]
        records = data.draw(st.permutations(records))
    mixed = _groups_mix(records, locations)
    assert mixed or not force_mix
    if mixed:
        with pytest.raises(MixedSourceError):
            build_bands(records, locations, SETTINGS)
    else:
        bands = build_bands(records, locations, SETTINGS)
        for b in bands:
            assert b.band_source == SYNTHETIC_SOURCE or SYNTHETIC_SOURCE not in b.band_source.split("+")


# Feature: hargaikan-v2, Property 10: Non-ok records are inert
@given(data=st.data(), locations=hierarchies())
def test_non_ok_records_are_inert_for_bands(data: st.DataObject, locations: dict) -> None:
    """**Validates: Requirements 3.14, 5.1**

    For any set of ok records and any added records with a non-ok Quality_Status (from any source,
    including SYNTHETIC), the bands from the union equal the bands from the ok records alone.
    """
    ids = list(locations)
    ok = data.draw(st.lists(price_records(ids), min_size=0, max_size=10))
    bad = data.draw(
        st.lists(
            price_records(ids, sources=[REAL_SOURCE, SYNTHETIC_SOURCE], statuses=NON_OK_STATUSES),
            min_size=0,
            max_size=10,
        )
    )
    union = data.draw(st.permutations(ok + bad))
    assert build_bands(union, locations, SETTINGS) == build_bands(ok, locations, SETTINGS)


# --- Examples ------------------------------------------------------------------------------------


def test_weighted_quantile_examples() -> None:
    from hargaikan.pipeline.bands import weighted_quantile

    assert weighted_quantile([10.0, 20.0], [1, 3], 0.25) == 10.0
    assert weighted_quantile([10.0, 20.0], [1, 3], 0.5) == 20.0
    assert weighted_quantile([30.0, 10.0, 20.0], [1, 1, 1], 0.5) == 20.0
    with pytest.raises(ValueError):
        weighted_quantile([], [], 0.5)
    with pytest.raises(ValueError):
        weighted_quantile([1.0], [0], 0.5)


def test_grade_bands_and_ancestors_example() -> None:
    from datetime import date

    from hargaikan.domain.models import PriceRecord
    from hargaikan.pipeline.locations import NASIONAL_ID, NEGERI_ID, build_hierarchy, by_id
    from hargaikan.pipeline.cleaning import default_location_id, normalise_premise

    premise = normalise_premise("AEON ( AU2 )")
    locs = by_id(build_hierarchy([premise], {}))
    pid = default_location_id(premise)
    rec = PriceRecord("sp", pid, "IKAN BAWAL HITAM (BESAR)", "BESAR", "kg", 20.0, None, None, 4,
                      date(2026, 10, 12), date(2026, 10, 18), "IKANSIGHT", 0, "ok")
    bands = build_bands([rec, dataclasses.replace(rec, quality_status="outlier", price_rm_per_kg=999.0)], locs, SETTINGS)
    keys = {(b.location_id, b.grade) for b in bands}
    assert keys == {(pid, None), (pid, "BESAR"), (NEGERI_ID, None), (NEGERI_ID, "BESAR"),
                    (NASIONAL_ID, None), (NASIONAL_ID, "BESAR")}
    assert all(b.median == 20.0 and b.record_count == 4 and b.confidence_level == "sederhana" for b in bands)
