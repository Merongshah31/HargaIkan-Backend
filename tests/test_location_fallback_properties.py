"""Tests for hargaikan.core.location_fallback.resolve_band (Property 16 plus examples)."""

from __future__ import annotations

from datetime import date

import pytest
from hypothesis import given
from hypothesis import strategies as st

from hargaikan.core.location_fallback import UnknownLocationError, resolve_band
from hargaikan.domain import messages as m
from hargaikan.domain.models import Location, PriceBand

SPECIES = "sp-target"
GRADES = ("A", "B")


def _band(species_id: str, location_id: str, grade: str | None, median: float = 10.0) -> PriceBand:
    return PriceBand(
        species_id=species_id,
        location_id=location_id,
        grade=grade,
        p25=median - 1,
        median=median,
        p75=median + 1,
        record_count=5,
        confidence_level="sederhana",
        period_start=date(2024, 1, 1),
        period_end=date(2024, 1, 14),
        band_source="IKANSIGHT",
    )


@st.composite
def hierarchies(draw: st.DrawFn) -> dict[str, Location]:
    """Random acyclic forest: node i's parent (if any) is a node with a smaller index."""
    n = draw(st.integers(min_value=1, max_value=8))
    locs: dict[str, Location] = {}
    for i in range(n):
        parent = None if i == 0 else draw(st.none() | st.integers(min_value=0, max_value=i - 1))
        lid = f"L{i}"
        locs[lid] = Location(lid, f"Lokasi {i}", "premis", None if parent is None else f"L{parent}", None, None)
    return locs


@st.composite
def scenarios(draw: st.DrawFn):  # type: ignore[no-untyped-def]
    locs = draw(hierarchies())
    ids = sorted(locs)
    keys = draw(st.sets(st.tuples(st.sampled_from(ids), st.sampled_from((None, *GRADES))), max_size=12))
    bands = [_band(SPECIES, lid, g, median=float(i + 2)) for i, (lid, g) in enumerate(sorted(keys, key=str))]
    # Bands for another species at arbitrary keys must be ignored.
    other = draw(st.sets(st.tuples(st.sampled_from(ids), st.sampled_from((None, *GRADES))), max_size=6))
    bands += [_band("sp-other", lid, g, median=99.0) for lid, g in other]
    bands = draw(st.permutations(bands))
    selected = draw(st.sampled_from(ids))
    grade = draw(st.none() | st.sampled_from((*GRADES, "C")))
    return locs, bands, selected, grade


def _oracle(locs, bands, selected, grade):  # type: ignore[no-untyped-def]
    """Independent walk up parent_id: grade-specific band first, then all-grade, per level."""
    by_key = {(b.location_id, b.grade): b for b in bands if b.species_id == SPECIES}
    current: str | None = selected
    while current is not None:
        if grade is not None and (current, grade) in by_key:
            return by_key[(current, grade)], current
        if (current, None) in by_key:
            return by_key[(current, None)], current
        current = locs[current].parent_id
    return None, None


# Feature: hargaikan-v2, Property 16: Location fallback picks the nearest band
@given(case=scenarios())
def test_location_fallback_picks_nearest_band(case) -> None:  # type: ignore[no-untyped-def]
    """*For any* acyclic hierarchy, set of bands, selected location and optional grade,
    `resolve_band` returns the band at the nearest ancestor-or-self location that has one, preferring
    a grade-specific band over the all-grade band at the same location. `is_fallback` is true if and
    only if the used location differs from the requested one. The message equals `FALLBACK_TEMPLATE`
    filled with both location names exactly when `is_fallback` is true. When no ancestor has a band,
    the band is None and the message is "Data belum tersedia".

    **Validates: Requirements 6.3, 6.4, 6.5, 6.6**
    """
    locs, bands, selected, grade = case
    res = resolve_band(selected, SPECIES, grade, bands, locs)
    expected_band, expected_loc = _oracle(locs, bands, selected, grade)

    assert res.requested == locs[selected]
    assert res.band == expected_band
    if expected_band is None:
        assert res.used is None
        assert res.is_fallback is False
        assert res.message == "Data belum tersedia"
    else:
        assert res.used == locs[expected_loc]
        assert res.is_fallback == (expected_loc != selected)
        if res.is_fallback:
            assert res.message == m.FALLBACK_TEMPLATE.format(requested=locs[selected].name, used=locs[expected_loc].name)
        else:
            assert res.message is None


# ----------------------------------------------------------------------------------- examples

MY = Location("my", "Malaysia", "nasional", None, None, None)
KL = Location("kl", "Kuala Lumpur", "negeri", "my", None, None)
CHOW_KIT = Location("ck", "Pasar Chow Kit", "pasar", "kl", None, None)
LOCS = {loc.id: loc for loc in (MY, KL, CHOW_KIT)}


def test_chow_kit_falls_back_to_kuala_lumpur_with_exact_message() -> None:
    kl_band = _band(SPECIES, "kl", None)
    res = resolve_band("ck", SPECIES, None, [kl_band], LOCS)
    assert res.band == kl_band
    assert res.used == KL
    assert res.is_fallback is True
    assert res.message == "Data khusus Pasar Chow Kit belum tersedia. Rujukan Kuala Lumpur digunakan."


def test_own_band_is_used_without_message() -> None:
    own, parent = _band(SPECIES, "ck", None, 8.0), _band(SPECIES, "kl", None, 9.0)
    res = resolve_band("ck", SPECIES, None, [parent, own], LOCS)
    assert (res.band, res.used, res.is_fallback, res.message) == (own, CHOW_KIT, False, None)


def test_grade_band_preferred_at_same_level_but_nearer_all_grade_wins() -> None:
    ck_all, ck_a, kl_a = _band(SPECIES, "ck", None, 8.0), _band(SPECIES, "ck", "A", 9.0), _band(SPECIES, "kl", "A", 7.0)
    assert resolve_band("ck", SPECIES, "A", [ck_all, ck_a, kl_a], LOCS).band == ck_a
    # No grade-A band at Chow Kit: the all-grade band there is nearer than Kuala Lumpur's grade-A band.
    assert resolve_band("ck", SPECIES, "A", [ck_all, kl_a], LOCS).band == ck_all
    # No grade requested: grade-specific bands are never used.
    assert resolve_band("ck", SPECIES, None, [ck_a, kl_a], LOCS).band is None


def test_no_band_anywhere_gives_no_data() -> None:
    res = resolve_band("ck", SPECIES, "A", [_band("sp-other", "kl", None)], LOCS)
    assert (res.band, res.used, res.is_fallback, res.message) == (None, None, False, m.NO_DATA)
    assert res.requested == CHOW_KIT


def test_unknown_location_raises_keyerror_subclass() -> None:
    with pytest.raises(UnknownLocationError):
        resolve_band("nope", SPECIES, None, [], LOCS)
    assert issubclass(UnknownLocationError, KeyError)


def test_cyclic_hierarchy_terminates() -> None:
    a = Location("a", "A", "premis", "b", None, None)
    b = Location("b", "B", "kawasan", "a", None, None)
    res = resolve_band("a", SPECIES, None, [], {"a": a, "b": b})
    assert res.message == m.NO_DATA
