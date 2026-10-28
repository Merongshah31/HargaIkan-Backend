"""Example tests for search/fuzzy_search.py and core/search_decision.py."""

from __future__ import annotations

import pytest

from hargaikan.core.search_decision import ScoredMatch, best_per_species, decide
from hargaikan.domain.models import Species, SpeciesAlias
from hargaikan.search.fuzzy_search import fuzzy_search
from hargaikan.search.types import ScoredMatch as ReexportedScoredMatch
from hargaikan.search.types import SearchResult

KEMBUNG = Species("id-kembung", "IKAN_KEMBUNG", "IKAN KEMBUNG", "Indian mackerel", ("ikan_bersirip",), True)
BAWAL_HITAM = Species("id-bh", "IKAN_BAWAL_HITAM", "IKAN BAWAL HITAM", None, ("ikan_bersirip",), True)
BAWAL_PUTIH = Species("id-bp", "IKAN_BAWAL_PUTIH", "IKAN BAWAL PUTIH", None, ("ikan_bersirip",), True)
TONGKOL = Species("id-tongkol", "IKAN_TONGKOL", "IKAN TONGKOL", "Tuna", ("ikan_bersirip",), True)
OLD = Species("id-old", "IKAN_KEMBONG_LAMA", "IKAN KEMBONG LAMA", None, (), False)
SPECIES = [KEMBUNG, BAWAL_HITAM, BAWAL_PUTIH, TONGKOL, OLD]
ALIASES = [
    SpeciesAlias("id-bh", "Black pomfret", "en"),
    SpeciesAlias("id-bp", "White pomfret", "en"),
    SpeciesAlias("id-kembung", "kembung", "local"),
    SpeciesAlias("id-old", "kembong", "raw"),  # inactive species: must never be returned
]


def _decide(matches):  # type: ignore[no-untyped-def]
    return decide(matches, high=0.80, margin=0.05, low=0.45, max_candidates=5)


def test_types_are_reexported() -> None:
    assert ReexportedScoredMatch is ScoredMatch
    assert SearchResult.__dataclass_fields__.keys() == {"status", "matches", "backend", "degraded", "notice"}


def test_misspelt_kembong_finds_kembung_not_inactive_species() -> None:
    found = fuzzy_search("kembong", SPECIES, ALIASES)
    assert found[0].species_id == KEMBUNG.id
    assert found[0].name_ms == "IKAN KEMBUNG"
    assert OLD.id not in {m.species_id for m in found}
    status, chosen = _decide(found)
    assert status in ("confident", "candidates")
    assert chosen[0].species_id == KEMBUNG.id


def test_bawal_itam_offers_bawal_hitam() -> None:
    found = fuzzy_search("bawal itam", SPECIES, ALIASES)
    assert found[0].species_id == BAWAL_HITAM.id
    status, chosen = _decide(found)
    # "itam" is equally close to "hitam" and "putih" under WRatio, so the user is asked to confirm.
    assert status == "candidates"
    assert BAWAL_HITAM.id in {m.species_id for m in chosen}


def test_english_alias_black_pomfret_is_confident() -> None:
    found = fuzzy_search("black pomfret", SPECIES, ALIASES)
    assert found[0].species_id == BAWAL_HITAM.id
    assert found[0].matched_alias == "Black pomfret"
    assert found[0].score == pytest.approx(1.0)
    status, chosen = _decide(found)
    assert status == "confident"
    assert chosen == (found[0],)


def test_pomfret_alone_returns_both_pomfrets_as_candidates() -> None:
    status, chosen = _decide(fuzzy_search("Pomfret", SPECIES, ALIASES))
    assert status == "candidates"
    assert {m.species_id for m in chosen[:2]} == {BAWAL_HITAM.id, BAWAL_PUTIH.id}


def test_case_and_whitespace_are_normalised() -> None:
    assert fuzzy_search("  IKAN   tongkol ", SPECIES, ALIASES)[0].score == pytest.approx(1.0)


@pytest.mark.parametrize("query", ["", "   ", "\t\n"])
def test_blank_query_returns_nothing(query: str) -> None:
    assert fuzzy_search(query, SPECIES, ALIASES) == []


def test_unrelated_query_is_none() -> None:
    assert _decide(fuzzy_search("zzzzqqqq", SPECIES, ALIASES)) == ("none", ())


def test_limit_and_sorting() -> None:
    found = fuzzy_search("ikan", SPECIES, ALIASES, limit=2)
    assert len(found) == 2
    assert found[0].score >= found[1].score
    assert fuzzy_search("ikan", SPECIES, ALIASES, limit=0) == []


def test_best_per_species_tie_breaks_deterministically() -> None:
    a = ScoredMatch("x", "IKAN X", "b", 0.9)
    b = ScoredMatch("x", "IKAN X", "a", 0.9)
    c = ScoredMatch("y", "IKAN Y", "c", 0.9)
    assert best_per_species([a, b, c]) == best_per_species([c, b, a]) == [b, c]


def test_decide_rules_examples() -> None:
    m = lambda sid, s: ScoredMatch(sid, f"N{sid}", sid, s)  # noqa: E731
    assert _decide([]) == ("none", ())
    assert _decide([m("a", 0.44)]) == ("none", ())
    assert _decide([m("a", 0.9)]) == ("confident", (m("a", 0.9),))
    assert _decide([m("a", 0.9), m("b", 0.86)])[0] == "candidates"  # lead 0.04 < margin
    assert _decide([m("a", 0.9), m("b", 0.85)])[0] == "confident"
    assert _decide([m("a", 0.7), m("b", 0.5), m("c", 0.3)]) == ("candidates", (m("a", 0.7), m("b", 0.5)))
    many = [m(str(i), 0.5 + i / 100) for i in range(8)]
    status, chosen = _decide(many)
    assert status == "candidates" and len(chosen) == 5
    # Duplicate entries for one species count once.
    assert _decide([m("a", 0.9), m("a", 0.88)]) == ("confident", (m("a", 0.9),))
    with pytest.raises(ValueError):
        decide([], 0.8, 0.05, 0.45, 0)
