"""Property tests for the search decision rules (core/search_decision.py)."""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from hargaikan.core.search_decision import ScoredMatch, best_per_species, decide
from hargaikan.domain.models import Species, SpeciesAlias
from hargaikan.search.fuzzy_search import fuzzy_search

SPECIES_IDS = [f"sp-{i}" for i in range(8)]
scores = st.floats(min_value=0.0, max_value=1.0, allow_nan=False)
matches_st = st.lists(
    st.builds(
        ScoredMatch,
        species_id=st.sampled_from(SPECIES_IDS),
        name_ms=st.sampled_from(SPECIES_IDS).map(lambda s: f"IKAN {s.upper()}"),
        matched_alias=st.text(alphabet="abc ", max_size=4),
        score=scores,
    ),
    max_size=20,
)


@st.composite
def thresholds(draw):  # type: ignore[no-untyped-def]
    low = draw(scores)
    high = draw(st.floats(min_value=low, max_value=1.0, allow_nan=False))
    margin = draw(st.floats(min_value=0.0, max_value=0.5, allow_nan=False))
    return high, margin, low


# Feature: hargaikan-v2, Property 30: Search decision rules
@given(matches=matches_st, th=thresholds(), max_candidates=st.integers(min_value=1, max_value=5))
def test_property_30_search_decision_rules(matches, th, max_candidates) -> None:  # type: ignore[no-untyped-def]
    """Confident ⇒ one leader ≥ high by ≥ margin; candidates ⇒ 1..max distinct species ≥ low, sorted
    descending, confident condition false; none ⇔ no species ≥ low.

    **Validates: Requirements 11.5, 11.6, 11.7, 11.9, 11.11**
    """
    high, margin, low = th
    status, chosen = decide(matches, high, margin, low, max_candidates)

    best: dict[str, float] = {}
    for m in matches:
        best[m.species_id] = max(best.get(m.species_id, float("-inf")), m.score)
    ranked = sorted(best.values(), reverse=True)
    top = ranked[0] if ranked else None
    second = ranked[1] if len(ranked) > 1 else float("-inf")
    confident_cond = top is not None and top >= high and top - second >= margin

    # Every returned match is one of the inputs and represents its species' best score.
    for c in chosen:
        assert c in matches
        assert c.score == best[c.species_id]

    if status == "none":
        assert chosen == ()
        assert top is None or top < low
    elif status == "confident":
        assert len(chosen) == 1
        assert chosen[0].score == top
        assert chosen[0].score >= high
        assert chosen[0].score - second >= margin
    else:
        assert status == "candidates"
        assert not confident_cond
        assert 1 <= len(chosen) <= max_candidates
        ids = [c.species_id for c in chosen]
        assert len(set(ids)) == len(ids)
        assert all(c.score >= low for c in chosen)
        assert [c.score for c in chosen] == sorted((c.score for c in chosen), reverse=True)
        # The highest-scoring species are the ones returned.
        eligible = [s for s in ranked if s >= low]
        assert [c.score for c in chosen] == eligible[:max_candidates]

    assert (status == "none") == (top is None or top < low)


# Feature: hargaikan-v2, Property 30: Search decision rules
@given(matches=matches_st, th=thresholds(), data=st.data())
def test_property_30_decision_is_order_independent(matches, th, data) -> None:  # type: ignore[no-untyped-def]
    """The decision and best-per-species output do not depend on match order.

    **Validates: Requirements 11.5, 11.6, 11.7, 11.9, 11.11**
    """
    shuffled = data.draw(st.permutations(matches))
    assert best_per_species(matches) == best_per_species(shuffled)
    assert decide(matches, *th, 5) == decide(shuffled, *th, 5)


name_st = st.text(alphabet="abcdefghij ", min_size=1, max_size=10)


@st.composite
def catalogs(draw):  # type: ignore[no-untyped-def]
    n = draw(st.integers(min_value=0, max_value=6))
    species = [
        Species(f"sp-{i}", f"CODE_{i}", draw(name_st), draw(st.none() | name_st), (), draw(st.booleans()))
        for i in range(n)
    ]
    ids = [s.id for s in species] + ["sp-unknown"]
    aliases = draw(
        st.lists(st.builds(SpeciesAlias, st.sampled_from(ids), name_st, st.sampled_from(["ms", "en", "local", "raw"])), max_size=10)
    )
    return species, aliases


# Feature: hargaikan-v2, Property 30: Search decision rules
@given(cat=catalogs(), query=st.text(alphabet="abcdefghij \t", max_size=10), th=thresholds())
def test_property_30_fuzzy_returns_only_active_species(cat, query, th) -> None:  # type: ignore[no-untyped-def]
    """The fuzzy backend only scores active catalog species; a blank query yields no matches.

    **Validates: Requirements 11.5, 11.6, 11.7, 11.9, 11.11**
    """
    species, aliases = cat
    active = {s.id for s in species if s.active}
    found = fuzzy_search(query, species, aliases)
    if not query.strip():
        assert found == []
    assert {m.species_id for m in found} <= active
    assert len({m.species_id for m in found}) == len(found)
    assert all(0.0 <= m.score <= 1.0 for m in found)
    _, chosen = decide(found, *th, 5)
    assert {m.species_id for m in chosen} <= active
