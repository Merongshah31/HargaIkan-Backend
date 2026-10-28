"""Property tests for the curated loader (pipeline/curated.py)."""

from __future__ import annotations

import yaml
from hypothesis import given
from hypothesis import strategies as st

from hargaikan.domain.ids import species_id
from hargaikan.domain.models import FishProfile, SimilarityEdge
from hargaikan.pipeline import curated as c

CODES = [f"IKAN_{x}" for x in "ABCDEFG"]
KNOWN = set(CODES)

word = st.text(alphabet="abcdefghijklmnopqrstuvwxyz _", min_size=1, max_size=12).filter(lambda s: s.strip())
# Broad Unicode for the round trip (no surrogates, which cannot be encoded), never blank.
rich_text = st.text(st.characters(blacklist_categories=("Cs",)), min_size=1, max_size=30).filter(lambda s: s.strip())
unit_score = st.floats(min_value=0.0, max_value=1.0, allow_nan=False)


# --------------------------------------------------------------------------- valid raw entries


@st.composite
def profile_dicts(draw):  # type: ignore[no-untyped-def]
    codes = draw(st.lists(st.sampled_from(CODES), unique=True, max_size=len(CODES)))
    return [
        {
            "species_code": code,
            "texture": draw(word),
            "taste": draw(word),
            "bone_level": draw(word),
            "cooking_methods": draw(st.lists(word, max_size=3)),
            "allergen_groups": draw(st.lists(word, max_size=2)),
            "notes": draw(st.none() | word),
        }
        for code in codes
    ]


PAIRS = [(a, b) for a in CODES for b in CODES if a != b]


@st.composite
def similarity_dicts(draw):  # type: ignore[no-untyped-def]
    pairs = draw(st.lists(st.sampled_from(PAIRS), unique=True, max_size=8))
    rows = []
    for src, dst in pairs:
        row = {"from": src, "to": dst, "similarity_score": draw(unit_score), "reason": draw(word),
               "basis": draw(st.lists(word, max_size=3))}
        verified = draw(st.none() | st.booleans())
        if verified is not None:
            row["verified"] = verified
        rows.append(row)
    return rows


# --------------------------------------------------------------------------- defect injection

SIM_DEFECTS = ("unknown_species", "missing_field", "invalid_score", "self_link")
PROFILE_DEFECTS = ("unknown_species", "missing_field")


def _inject(draw, rows, defects, required, code_fields):  # type: ignore[no-untyped-def]
    """Inject at most one defect into a non-empty subset of rows; return the expected problems."""
    targets = draw(st.lists(st.integers(0, len(rows) - 1), min_size=1, unique=True))
    expected: list[tuple[int, str, str]] = []
    for i in targets:
        kind = draw(st.sampled_from(defects))
        row = rows[i]
        if kind == "unknown_species":
            field = draw(st.sampled_from(code_fields))
            row[field] = f"TIDAK_DIKENALI_{i}"
            expected.append((i, field, c.UNKNOWN_SPECIES))
        elif kind == "missing_field":
            field = draw(st.sampled_from(required))
            del row[field]
            expected.append((i, field, c.MISSING_FIELD))
        elif kind == "invalid_score":
            row["similarity_score"] = draw(
                st.floats(min_value=1.0, exclude_min=True, allow_nan=False)
                | st.floats(max_value=0.0, exclude_max=True, allow_nan=False)
                | st.just(float("nan"))
                | word
            )
            expected.append((i, "similarity_score", c.INVALID_SCORE))
        else:  # self_link
            row["to"] = row["from"]
            expected.append((i, "to", c.SELF_LINK))
    return expected


@st.composite
def defective_similarity(draw):  # type: ignore[no-untyped-def]
    rows = draw(similarity_dicts().filter(bool))
    return rows, _inject(draw, rows, SIM_DEFECTS, c.SIMILARITY_REQUIRED, ("from", "to"))


@st.composite
def defective_profiles(draw):  # type: ignore[no-untyped-def]
    rows = draw(profile_dicts().filter(bool))
    return rows, _inject(draw, rows, PROFILE_DEFECTS, c.PROFILE_REQUIRED, ("species_code",))


def _assert_rejected_with(loader, rows, expected) -> None:  # type: ignore[no-untyped-def]
    text = yaml.safe_dump(rows, allow_unicode=True, sort_keys=False)
    try:
        loaded = loader(text, KNOWN)
    except c.CuratedError as exc:
        found = {(p.entry, p.field, p.kind) for p in exc.problems}
        missing = [e for e in expected if e not in found]
        assert not missing, (missing, [str(p) for p in exc.problems])
        for p in exc.problems:
            assert str(p) in str(exc)
    else:  # pragma: no cover - the property fails here
        raise AssertionError(f"loader accepted a defective file and returned {loaded!r}")


# Feature: hargaikan-v2, Property 37: Curated loader rejects whole files with every problem listed
@given(case=defective_similarity())
def test_property_37_similarity_rejects_whole_file(case) -> None:  # type: ignore[no-untyped-def]
    """Injected unknown codes, missing fields, out-of-range scores and self-links are all listed,
    and no edges are returned.

    **Validates: Requirements 15.1, 15.2**
    """
    rows, expected = case
    _assert_rejected_with(c.load_similarity, rows, expected)


# Feature: hargaikan-v2, Property 37: Curated loader rejects whole files with every problem listed
@given(case=defective_profiles())
def test_property_37_profiles_reject_whole_file(case) -> None:  # type: ignore[no-untyped-def]
    """Injected unknown codes and missing fields are all listed, and no profiles are returned.

    **Validates: Requirements 15.1, 15.2**
    """
    rows, expected = case
    _assert_rejected_with(c.load_profiles, rows, expected)


# --------------------------------------------------------------------------- round trip


@st.composite
def profiles(draw):  # type: ignore[no-untyped-def]
    codes = draw(st.lists(st.sampled_from(CODES), unique=True, max_size=len(CODES)))
    return [
        FishProfile(code, draw(rich_text), draw(rich_text), draw(rich_text),
                    tuple(draw(st.lists(rich_text, max_size=3))), tuple(draw(st.lists(rich_text, max_size=3))),
                    draw(st.none() | st.text(st.characters(blacklist_categories=("Cs",)), max_size=30)))
        for code in codes
    ]


@st.composite
def edges(draw):  # type: ignore[no-untyped-def]
    pairs = draw(st.lists(st.sampled_from(PAIRS), unique=True, max_size=8))
    return [
        SimilarityEdge(species_id(a), species_id(b), draw(unit_score), draw(rich_text),
                       tuple(draw(st.lists(rich_text, max_size=3))), draw(st.booleans()))
        for a, b in pairs
    ]


def _upsert(store: dict, items, key) -> None:  # type: ignore[no-untyped-def]
    """Stand-in for an idempotent upsert keyed by the natural key."""
    for item in items:
        store[key(item)] = item


# Feature: hargaikan-v2, Property 38: Curated data round trip
@given(items=profiles(), sims=edges())
def test_property_38_round_trip(items, sims) -> None:  # type: ignore[no-untyped-def]
    """dump then load returns equal profiles and edges, and loading the same content twice stores
    the same rows as loading it once.

    **Validates: Requirements 15.3, 15.5**
    """
    p_text, s_text = c.dump_profiles(items), c.dump_similarity(sims, CODES)
    assert c.load_profiles(p_text, KNOWN) == items
    assert c.load_similarity(s_text, KNOWN) == sims

    once: dict = {}
    twice: dict = {}
    _upsert(once, c.load_similarity(s_text, KNOWN), lambda e: (e.from_species_id, e.to_species_id))
    for _ in range(2):
        _upsert(twice, c.load_similarity(s_text, KNOWN), lambda e: (e.from_species_id, e.to_species_id))
    assert once == twice
    p_once: dict = {}
    p_twice: dict = {}
    _upsert(p_once, c.load_profiles(p_text, KNOWN), lambda p: p.species_code)
    for _ in range(2):
        _upsert(p_twice, c.load_profiles(p_text, KNOWN), lambda p: p.species_code)
    assert p_once == p_twice


# Feature: hargaikan-v2, Property 38: Curated data round trip
@given(rows=similarity_dicts())
def test_property_38_verified_defaults_to_false(rows) -> None:  # type: ignore[no-untyped-def]
    """An entry without a verified field loads with verified false; explicit values are kept.

    **Validates: Requirements 15.3, 15.5**
    """
    loaded = c.load_similarity(yaml.safe_dump(rows, sort_keys=False), KNOWN)
    assert [e.verified for e in loaded] == [r.get("verified", False) for r in rows]
    assert [(e.from_species_id, e.to_species_id) for e in loaded] == [
        (species_id(r["from"]), species_id(r["to"])) for r in rows
    ]
