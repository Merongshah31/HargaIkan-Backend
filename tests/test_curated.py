"""Example tests for pipeline/curated.py."""

from __future__ import annotations

import pytest

from hargaikan.domain.ids import species_id
from hargaikan.domain.models import FishProfile, SimilarityEdge
from hargaikan.pipeline import curated as c

CODES = {"IKAN_BAWAL_HITAM", "IKAN_BAWAL_PUTIH", "IKAN_KEMBUNG"}

PROFILES_YAML = """
- species_code: IKAN_BAWAL_HITAM
  texture: lembut
  taste: manis sederhana
  bone_level: sederhana
  cooking_methods: [goreng, stim, masak asam]
  allergen_groups: [ikan_bersirip]
  notes: null
"""

SIMILARITY_YAML = """
- from: IKAN_BAWAL_HITAM
  to: IKAN_BAWAL_PUTIH
  similarity_score: 0.8
  reason: Bentuk badan dan tekstur isi hampir sama.
  basis: [tekstur, kaedah_masakan]
  verified: false
- from: IKAN_BAWAL_PUTIH
  to: IKAN_BAWAL_HITAM
  similarity_score: 1
  reason: Sama.
  basis: []
"""


def _kinds(exc: c.CuratedError) -> list[tuple[int | None, str | None, str]]:
    return [(p.entry, p.field, p.kind) for p in exc.problems]


def test_design_example_profiles_load() -> None:
    assert c.load_profiles(PROFILES_YAML, CODES) == [
        FishProfile("IKAN_BAWAL_HITAM", "lembut", "manis sederhana", "sederhana",
                    ("goreng", "stim", "masak asam"), ("ikan_bersirip",), None)
    ]


def test_similarity_maps_codes_to_species_ids_and_defaults_verified() -> None:
    edges = c.load_similarity(SIMILARITY_YAML, CODES)
    assert edges[0] == SimilarityEdge(species_id("IKAN_BAWAL_HITAM"), species_id("IKAN_BAWAL_PUTIH"), 0.8,
                                      "Bentuk badan dan tekstur isi hampir sama.", ("tekstur", "kaedah_masakan"), False)
    assert edges[1].verified is False  # no verified field → False
    assert edges[1].similarity_score == 1.0 and isinstance(edges[1].similarity_score, float)


def test_custom_id_mapping() -> None:
    edges = c.load_similarity(SIMILARITY_YAML, CODES, id_for=lambda code: f"id:{code}")
    assert edges[0].from_species_id == "id:IKAN_BAWAL_HITAM"
    text = c.dump_similarity(edges, CODES, id_for=lambda code: f"id:{code}")
    assert c.load_similarity(text, CODES, id_for=lambda code: f"id:{code}") == edges


@pytest.mark.parametrize("text", ["", "   \n", "# only a comment\n", "[]"])
def test_empty_files_load_nothing(text: str) -> None:
    assert c.load_profiles(text, CODES) == []
    assert c.load_similarity(text, CODES) == []


def test_every_similarity_problem_is_listed_and_nothing_loads() -> None:
    text = """
- from: IKAN_BAWAL_HITAM
  to: IKAN_TIDAK_WUJUD
  similarity_score: 0.5
  reason: x
  basis: []
- from: IKAN_KEMBUNG
  to: IKAN_KEMBUNG
  similarity_score: 1.5
  reason: x
  basis: []
- from: IKAN_BAWAL_HITAM
  similarity_score: abc
  reason: ""
  basis: [ok]
  verified: "yes please"
  verfied: true
- from: IKAN_BAWAL_HITAM
  to: IKAN_TIDAK_WUJUD
  similarity_score: .nan
  reason: dup
  basis: notalist
- just a string
"""
    with pytest.raises(c.CuratedError) as info:
        c.load_similarity(text, CODES)
    kinds = _kinds(info.value)
    assert (0, "to", c.UNKNOWN_SPECIES) in kinds
    assert (1, "to", c.SELF_LINK) in kinds
    assert (1, "similarity_score", c.INVALID_SCORE) in kinds
    assert (2, "to", c.MISSING_FIELD) in kinds
    assert (2, "similarity_score", c.INVALID_SCORE) in kinds
    assert (2, "reason", c.INVALID_TYPE) in kinds
    assert (2, "verified", c.INVALID_TYPE) in kinds
    assert (2, "verfied", c.UNKNOWN_FIELD) in kinds
    assert (3, None, c.DUPLICATE) in kinds
    assert (3, "similarity_score", c.INVALID_SCORE) in kinds
    assert (3, "basis", c.INVALID_TYPE) in kinds
    assert (4, None, c.NOT_A_MAPPING) in kinds
    # The message names every problem.
    message = str(info.value)
    for p in info.value.problems:
        assert str(p) in message


@pytest.mark.parametrize("score", ["true", "-0.01", "1.0001", ".inf", "'0.5'"])
def test_bad_scores_rejected(score: str) -> None:
    text = f"- {{from: IKAN_BAWAL_HITAM, to: IKAN_KEMBUNG, similarity_score: {score}, reason: r, basis: []}}"
    with pytest.raises(c.CuratedError) as info:
        c.load_similarity(text, CODES)
    assert _kinds(info.value) == [(0, "similarity_score", c.INVALID_SCORE)]


def test_profile_problems_listed() -> None:
    text = """
- species_code: IKAN_LAIN
  texture: lembut
  taste: manis
  bone_level: sedikit
  cooking_methods: [goreng]
  allergen_groups: []
- species_code: IKAN_KEMBUNG
  texture: lembut
  bone_level: sedikit
  cooking_methods: goreng
  allergen_groups: [ikan_bersirip]
  notes: 3
- species_code: IKAN_KEMBUNG
  texture: a
  taste: b
  bone_level: c
  cooking_methods: []
  allergen_groups: []
"""
    with pytest.raises(c.CuratedError) as info:
        c.load_profiles(text, CODES)
    kinds = _kinds(info.value)
    assert (0, "species_code", c.UNKNOWN_SPECIES) in kinds
    assert (1, "taste", c.MISSING_FIELD) in kinds
    assert (1, "cooking_methods", c.INVALID_TYPE) in kinds
    assert (1, "notes", c.INVALID_TYPE) in kinds
    assert (2, "species_code", c.DUPLICATE) in kinds


@pytest.mark.parametrize("text, kind", [("a: [unclosed", c.INVALID_YAML), ("key: value", c.NOT_A_LIST)])
def test_file_level_problems(text: str, kind: str) -> None:
    for loader in (c.load_profiles, c.load_similarity):
        with pytest.raises(c.CuratedError) as info:
            loader(text, CODES)
        assert [(p.entry, p.kind) for p in info.value.problems] == [(None, kind)]


def test_dump_similarity_rejects_unknown_ids() -> None:
    edge = SimilarityEdge("not-an-id", species_id("IKAN_KEMBUNG"), 0.5, "r", (), False)
    with pytest.raises(c.CuratedError) as info:
        c.dump_similarity([edge], CODES)
    assert _kinds(info.value) == [(0, "from", c.UNKNOWN_ID)]


def test_dump_writes_verified_explicitly() -> None:
    edges = c.load_similarity(SIMILARITY_YAML, CODES)
    assert c.dump_similarity(edges, CODES).count("verified: false") == 2
