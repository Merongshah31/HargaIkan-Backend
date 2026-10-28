"""Search service: identify a species from a fish name or alias (Requirement 11).

Layer 3 (no FastAPI imports). Order:

1. Empty query after normalisation → ``empty_query`` with SEARCH_EMPTY.
2. Exact normalised canonical name or alias in the catalog → one confident match, backend "exact".
3. ``VectorSearch`` (Pinecone + embeddings) when configured. **Not wired in the MVP** (``None``):
   a later implementation embeds the query with the same model as ``scripts/build_index.py``,
   queries Pinecone with ``Settings.service_timeout_s`` and returns :class:`ScoredMatch` items.
   If it raises, search falls back to fuzzy with SEARCH_FALLBACK_NOTICE (``degraded=True``).
4. rapidfuzz over the catalog (the MVP default, ``degraded=False``).

The same :func:`decide` rules (Settings thresholds) apply to both scored backends. Only active
catalog species are returned, and names always come from the catalog, never index metadata.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from hargaikan.config import Settings
from hargaikan.core.search_decision import ScoredMatch, SearchBackend, SearchStatus, decide
from hargaikan.core.text import normalise_term
from hargaikan.domain import messages as m
from hargaikan.repositories.supabase_client import safe_error
from hargaikan.search.fuzzy_search import fuzzy_search
from hargaikan.services.catalog_service import CatalogService, CatalogSnapshot

logger = logging.getLogger(__name__)


@runtime_checkable
class VectorSearch(Protocol):
    """Semantic species lookup (Pinecone). Returns raw matches; the service filters and decides."""

    def query(self, text: str, top_k: int, timeout_s: float) -> list[ScoredMatch]: ...


@dataclass(frozen=True)
class SearchView:
    query: str
    status: SearchStatus
    matches: tuple[ScoredMatch, ...]
    backend: SearchBackend
    degraded: bool
    notice: str | None
    confirm_prompt: str | None  # SEARCH_CONFIRM when status == "candidates"


def _join(*parts: str | None) -> str | None:
    text = " ".join(p for p in parts if p)
    return text or None


class SearchService:
    def __init__(self, catalog: CatalogService, settings: Settings, vector: VectorSearch | None = None) -> None:
        self._catalog = catalog
        self._s = settings
        self._vector = vector

    @property
    def vector_connected(self) -> bool:
        return self._vector is not None

    def _from_vector(self, q: str, snap: CatalogSnapshot) -> list[ScoredMatch]:
        assert self._vector is not None
        raw = self._vector.query(q, 10, self._s.service_timeout_s)
        out = []
        for hit in raw:
            sp = snap.species_by_id.get(hit.species_id)
            if sp is None:  # inactive or unknown in the catalog
                continue
            out.append(ScoredMatch(sp.id, sp.canonical_name_ms, hit.matched_alias, min(1.0, max(0.0, float(hit.score)))))
        return out

    def search(self, query: str | None) -> SearchView:
        raw = query or ""
        q = normalise_term(raw)
        if not q:
            return SearchView(raw, "empty_query", (), "exact", False, m.SEARCH_EMPTY, None)
        snap = self._catalog.snapshot()  # ServiceUnavailable propagates

        sid = snap.exact_index.get(q)
        if sid is not None:
            sp = snap.species_by_id[sid]
            return SearchView(raw, "confident", (ScoredMatch(sp.id, sp.canonical_name_ms, raw.strip(), 1.0),), "exact", False, None, None)

        backend: SearchBackend = "fuzzy"
        degraded, notice = False, None
        matches: list[ScoredMatch] | None = None
        if self._vector is not None:
            try:
                matches = self._from_vector(q, snap)
                backend = "pinecone"
            except Exception as exc:  # Pinecone / embedding failure or timeout
                logger.warning("vector search failed, using fuzzy fallback: %s", safe_error(exc))
                degraded, notice = True, m.SEARCH_FALLBACK_NOTICE
        if matches is None:
            matches = fuzzy_search(q, snap.species, snap.aliases)

        status, chosen = decide(
            matches, self._s.search_high, self._s.search_margin, self._s.search_low, self._s.search_max_candidates
        )
        if status == "none":
            return SearchView(raw, "none", (), backend, degraded, _join(notice, m.SEARCH_NOT_FOUND), None)
        confirm = m.SEARCH_CONFIRM if status == "candidates" else None
        return SearchView(raw, status, chosen, backend, degraded, notice, confirm)
