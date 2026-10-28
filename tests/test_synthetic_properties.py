"""Property tests for hargaikan.pipeline.synthetic (Property 20 of the design)."""

from __future__ import annotations

from datetime import date

from hypothesis import given
from hypothesis import strategies as st

from hargaikan.domain.models import Location, Species
from hargaikan.pipeline.synthetic import SYNTHETIC_SOURCE, generate

species_lists = st.lists(st.uuids().map(str), min_size=0, max_size=4, unique=True).map(
    lambda ids: [Species(i, f"SP_{n}", f"IKAN {n}", None, (), True) for n, i in enumerate(ids)]
)
premise_lists = st.lists(st.uuids().map(str), min_size=0, max_size=4, unique=True).map(
    lambda ids: [Location(i, f"PREMIS {n}", "premis", "kl", None, None) for n, i in enumerate(ids)]
)


# Feature: hargaikan-v2, Property 20: Synthetic generator is tagged and deterministic
@given(
    species=species_lists,
    premises=premise_lists,
    weeks=st.integers(min_value=0, max_value=8),
    seed=st.integers(min_value=-(2**63), max_value=2**63),
    start=st.dates(min_value=date(2020, 1, 1), max_value=date(2030, 12, 31)),
)
def test_synthetic_generator_is_tagged_and_deterministic(species, premises, weeks, seed, start) -> None:
    """**Validates: Requirements 8.1, 8.2**

    For any species list, premise list, week count and seed, every generated record has
    source_name SYNTHETIC, and two calls with the same arguments return equal record lists.
    """
    first = generate(species, premises, weeks, seed, start)
    second = generate(species, premises, weeks, seed, start)
    assert first == second
    assert all(r.source_name == SYNTHETIC_SOURCE for r in first)
    assert len(first) == len(species) * len(premises) * weeks
    for r in first:  # every record is publishable
        assert r.quality_status == "ok" and r.unit == "kg"
        assert r.price_rm_per_kg >= 1.0
        assert r.price_low <= r.price_rm_per_kg <= r.price_high
        assert r.record_count >= 1 and r.date_start <= r.date_end
    keys = [(r.species_id, r.location_id, r.date_start) for r in first]
    assert len(keys) == len(set(keys))
