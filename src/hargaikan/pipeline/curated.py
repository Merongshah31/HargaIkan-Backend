"""Curated fish profiles and similarity YAML: validate, load and dump (Requirement 15).

Pure logic: parses and emits YAML *text* only. Reading and writing files is the caller's job
(``scripts/load_curated.py``). No supabase, pinecone, requests or streamlit imports.

Loading validates every entry first and collects every problem. If there is any problem the whole
file is rejected with one ``CuratedError`` listing all of them, and nothing is returned.

Species references: the YAML uses species *codes* (e.g. ``IKAN_BAWAL_HITAM``), while
``SimilarityEdge`` uses species *ids*. ``load_similarity`` maps each code to an id with ``id_for``,
which defaults to ``hargaikan.domain.ids.species_id`` (``uuid5(NAMESPACE, code)``), the same id the
cleaning pipeline assigns. ``dump_similarity`` reverses this from the known codes.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any

import yaml

from hargaikan.domain.ids import species_id
from hargaikan.domain.models import FishProfile, SimilarityEdge

# Problem kinds (stable identifiers; tests and scripts match on them).
INVALID_YAML = "invalid_yaml"
NOT_A_LIST = "not_a_list"
NOT_A_MAPPING = "not_a_mapping"
MISSING_FIELD = "missing_field"
UNKNOWN_FIELD = "unknown_field"
INVALID_TYPE = "invalid_type"
UNKNOWN_SPECIES = "unknown_species"
INVALID_SCORE = "invalid_score"
SELF_LINK = "self_link"
DUPLICATE = "duplicate"
UNKNOWN_ID = "unknown_id"

PROFILE_REQUIRED = ("species_code", "texture", "taste", "bone_level", "cooking_methods", "allergen_groups")
PROFILE_OPTIONAL = ("notes",)
SIMILARITY_REQUIRED = ("from", "to", "similarity_score", "reason", "basis")
SIMILARITY_OPTIONAL = ("verified",)


@dataclass(frozen=True)
class CuratedProblem:
    """One validation problem. ``entry`` is the 0-based list index, or None for file-level problems."""

    entry: int | None
    field: str | None
    kind: str
    detail: str

    def __str__(self) -> str:
        where = "file" if self.entry is None else f"entry {self.entry}"
        if self.field is not None:
            where += f", field '{self.field}'"
        return f"{where}: {self.kind}: {self.detail}"


class CuratedError(ValueError):
    """Raised when a curated file has one or more problems. ``problems`` lists every one."""

    def __init__(self, problems: Sequence[CuratedProblem]) -> None:
        self.problems: tuple[CuratedProblem, ...] = tuple(problems)
        lines = "\n".join(f"- {p}" for p in self.problems)
        super().__init__(f"Curated file rejected with {len(self.problems)} problem(s):\n{lines}")


# --------------------------------------------------------------------------- helpers


def _parse_list(text: str) -> tuple[list[Any], list[CuratedProblem]]:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        return [], [CuratedProblem(None, None, INVALID_YAML, str(exc).replace("\n", " "))]
    if data is None:
        return [], []
    if not isinstance(data, list):
        return [], [CuratedProblem(None, None, NOT_A_LIST, f"top level is {type(data).__name__}, expected a list")]
    return data, []


def _check_keys(i: int, entry: dict, required: Sequence[str], optional: Sequence[str]) -> list[CuratedProblem]:
    problems = [CuratedProblem(i, f, MISSING_FIELD, "required field is missing") for f in required if f not in entry]
    allowed = set(required) | set(optional)
    problems += [
        CuratedProblem(i, str(k), UNKNOWN_FIELD, "field is not part of the schema")
        for k in entry
        if k not in allowed
    ]
    return problems


def _is_text(v: object) -> bool:
    return isinstance(v, str) and v.strip() != ""


def _check_text(i: int, entry: dict, field: str) -> list[CuratedProblem]:
    if field in entry and not _is_text(entry[field]):
        return [CuratedProblem(i, field, INVALID_TYPE, "must be a non-empty string")]
    return []


def _check_text_list(i: int, entry: dict, field: str) -> list[CuratedProblem]:
    if field not in entry:
        return []
    v = entry[field]
    if not isinstance(v, list) or not all(_is_text(x) for x in v):
        return [CuratedProblem(i, field, INVALID_TYPE, "must be a list of non-empty strings")]
    return []


def _check_code(i: int, entry: dict, field: str, known_codes: set[str]) -> list[CuratedProblem]:
    if field not in entry:
        return []
    v = entry[field]
    if not _is_text(v):
        return [CuratedProblem(i, field, INVALID_TYPE, "species code must be a non-empty string")]
    if v not in known_codes:
        return [CuratedProblem(i, field, UNKNOWN_SPECIES, f"unknown species code {v!r}")]
    return []


def _score_problem(i: int, v: object) -> CuratedProblem | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return CuratedProblem(i, "similarity_score", INVALID_SCORE, f"must be a number in [0, 1], got {v!r}")
    if not math.isfinite(v) or not 0.0 <= float(v) <= 1.0:
        return CuratedProblem(i, "similarity_score", INVALID_SCORE, f"must be in [0, 1], got {v!r}")
    return None


def _dump(rows: list[dict]) -> str:
    # allow_unicode=False escapes every non-ASCII character. With allow_unicode=True PyYAML writes
    # characters such as U+0085 (NEL) or U+2028 raw, and the loader then treats them as line breaks,
    # so the text would not round-trip. Bahasa Melayu curated text is ASCII, so readability is kept.
    return yaml.safe_dump(rows, allow_unicode=False, sort_keys=False, default_flow_style=False)


# --------------------------------------------------------------------------- profiles


def load_profiles(text: str, known_codes: set[str]) -> list[FishProfile]:
    """Parse and validate ``fish_profiles.yaml`` text. Raises ``CuratedError`` listing every problem."""
    data, problems = _parse_list(text)
    seen: dict[str, int] = {}
    for i, entry in enumerate(data):
        if not isinstance(entry, dict):
            problems.append(CuratedProblem(i, None, NOT_A_MAPPING, "entry must be a mapping"))
            continue
        problems += _check_keys(i, entry, PROFILE_REQUIRED, PROFILE_OPTIONAL)
        problems += _check_code(i, entry, "species_code", known_codes)
        for f in ("texture", "taste", "bone_level"):
            problems += _check_text(i, entry, f)
        for f in ("cooking_methods", "allergen_groups"):
            problems += _check_text_list(i, entry, f)
        notes = entry.get("notes")
        if notes is not None and not isinstance(notes, str):
            problems.append(CuratedProblem(i, "notes", INVALID_TYPE, "must be a string or null"))
        code = entry.get("species_code")
        if isinstance(code, str) and code.strip():
            if code in seen:
                problems.append(
                    CuratedProblem(i, "species_code", DUPLICATE, f"duplicate profile for {code!r} (first at entry {seen[code]})")
                )
            else:
                seen[code] = i
    if problems:
        raise CuratedError(problems)
    return [
        FishProfile(
            species_code=e["species_code"],
            texture=e["texture"],
            taste=e["taste"],
            bone_level=e["bone_level"],
            cooking_methods=tuple(e["cooking_methods"]),
            allergen_groups=tuple(e["allergen_groups"]),
            notes=e.get("notes"),
        )
        for e in data
    ]


def dump_profiles(items: Sequence[FishProfile]) -> str:
    """YAML text for ``items`` in the curated schema. ``load_profiles(dump_profiles(x), codes) == x``."""
    return _dump(
        [
            {
                "species_code": p.species_code,
                "texture": p.texture,
                "taste": p.taste,
                "bone_level": p.bone_level,
                "cooking_methods": list(p.cooking_methods),
                "allergen_groups": list(p.allergen_groups),
                "notes": p.notes,
            }
            for p in items
        ]
    )


# --------------------------------------------------------------------------- similarity


def load_similarity(
    text: str,
    known_codes: set[str],
    id_for: Callable[[str], str] = species_id,
) -> list[SimilarityEdge]:
    """Parse and validate ``fish_similarity.yaml`` text. Raises ``CuratedError`` listing every problem.

    ``verified`` defaults to False and must be a boolean when present. Edges are directed, so
    (A, B) and (B, A) are distinct; the same (from, to) pair twice is a duplicate.
    ``from``/``to`` codes become ``from_species_id``/``to_species_id`` via ``id_for``.
    """
    data, problems = _parse_list(text)
    seen: dict[tuple[str, str], int] = {}
    for i, entry in enumerate(data):
        if not isinstance(entry, dict):
            problems.append(CuratedProblem(i, None, NOT_A_MAPPING, "entry must be a mapping"))
            continue
        problems += _check_keys(i, entry, SIMILARITY_REQUIRED, SIMILARITY_OPTIONAL)
        problems += _check_code(i, entry, "from", known_codes)
        problems += _check_code(i, entry, "to", known_codes)
        if "similarity_score" in entry and (p := _score_problem(i, entry["similarity_score"])) is not None:
            problems.append(p)
        problems += _check_text(i, entry, "reason")
        problems += _check_text_list(i, entry, "basis")
        if "verified" in entry and not isinstance(entry["verified"], bool):
            problems.append(CuratedProblem(i, "verified", INVALID_TYPE, "must be true or false"))
        src, dst = entry.get("from"), entry.get("to")
        if _is_text(src) and _is_text(dst):
            if src == dst:
                problems.append(CuratedProblem(i, "to", SELF_LINK, f"{src!r} is linked to itself"))
            pair = (src, dst)
            if pair in seen:
                problems.append(
                    CuratedProblem(i, None, DUPLICATE, f"duplicate pair {src!r} → {dst!r} (first at entry {seen[pair]})")
                )
            else:
                seen[pair] = i
    if problems:
        raise CuratedError(problems)
    return [
        SimilarityEdge(
            from_species_id=id_for(e["from"]),
            to_species_id=id_for(e["to"]),
            similarity_score=float(e["similarity_score"]),
            reason=e["reason"],
            basis=tuple(e["basis"]),
            verified=e.get("verified", False),
        )
        for e in data
    ]


def dump_similarity(
    items: Sequence[SimilarityEdge],
    known_codes: Iterable[str],
    id_for: Callable[[str], str] = species_id,
) -> str:
    """YAML text for ``items`` with species ids mapped back to codes.

    ``known_codes`` is needed because ids are one-way hashes of codes. Raises ``CuratedError`` if an
    edge references an id that no known code maps to.
    ``load_similarity(dump_similarity(x, codes), set(codes)) == x``.
    """
    code_of = {id_for(c): c for c in known_codes}
    problems = [
        CuratedProblem(i, f, UNKNOWN_ID, f"no known species code for id {sid!r}")
        for i, e in enumerate(items)
        for f, sid in (("from", e.from_species_id), ("to", e.to_species_id))
        if sid not in code_of
    ]
    if problems:
        raise CuratedError(problems)
    return _dump(
        [
            {
                "from": code_of[e.from_species_id],
                "to": code_of[e.to_species_id],
                "similarity_score": float(e.similarity_score),
                "reason": e.reason,
                "basis": list(e.basis),
                "verified": bool(e.verified),
            }
            for e in items
        ]
    )
