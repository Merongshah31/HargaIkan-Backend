"""Search result types and the pure decision rules shared by the Pinecone and fuzzy backends.

Pure logic: no I/O and no supabase, pinecone, requests or streamlit imports.
``ScoredMatch`` and ``SearchResult`` live here (and are re-exported by ``hargaikan.search.types``)
so they can be imported without sentence-transformers or pinecone.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

SearchStatus = Literal["confident", "candidates", "none", "empty_query"]
SearchBackend = Literal["exact", "pinecone", "fuzzy"]
DecisionStatus = Literal["confident", "candidates", "none"]


@dataclass(frozen=True)
class ScoredMatch:
    species_id: str
    name_ms: str  # canonical Malay name from the Supabase catalog, never from index metadata
    matched_alias: str  # the name or alias that produced this score
    score: float  # [0, 1]


@dataclass(frozen=True)
class SearchResult:
    status: SearchStatus
    matches: tuple[ScoredMatch, ...]
    backend: SearchBackend
    degraded: bool
    notice: str | None


def _rank_key(m: ScoredMatch) -> tuple[float, str, str]:
    """Score descending, then name and id so ties are deterministic."""
    return (-m.score, m.name_ms, m.species_id)


def _better(a: ScoredMatch, b: ScoredMatch) -> bool:
    """True when ``a`` should represent its species instead of ``b`` (same species)."""
    if a.score != b.score:
        return a.score > b.score
    return (a.matched_alias, a.name_ms) < (b.matched_alias, b.name_ms)


def best_per_species(matches: Iterable[ScoredMatch]) -> list[ScoredMatch]:
    """Keep the highest-scoring match per species, ranked by score descending.

    Ties within a species keep the lexicographically smallest ``matched_alias``, so the result
    does not depend on input order.
    """
    best: dict[str, ScoredMatch] = {}
    for m in matches:
        current = best.get(m.species_id)
        if current is None or _better(m, current):
            best[m.species_id] = m
    return sorted(best.values(), key=_rank_key)


def decide(
    matches: Iterable[ScoredMatch],
    high: float,
    margin: float,
    low: float,
    max_candidates: int,
) -> tuple[DecisionStatus, tuple[ScoredMatch, ...]]:
    """Apply the search result rules (Requirements 11.5–11.7).

    - ``none`` when no species scores at least ``low``.
    - ``confident`` with exactly one match when the best species scores at least ``high`` and leads
      the second-best species by at least ``margin``.
    - otherwise ``candidates``: up to ``max_candidates`` distinct species scoring at least ``low``,
      sorted by score descending.
    """
    if max_candidates < 1:
        raise ValueError("max_candidates must be at least 1")
    ranked = best_per_species(matches)
    if not ranked or ranked[0].score < low:
        return "none", ()
    second = ranked[1].score if len(ranked) > 1 else float("-inf")
    if ranked[0].score >= high and ranked[0].score - second >= margin:
        return "confident", (ranked[0],)
    return "candidates", tuple(m for m in ranked if m.score >= low)[:max_candidates]
