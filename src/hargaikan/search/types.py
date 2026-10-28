"""Lightweight search types. Import from here in services and pages.

Re-exports the dataclasses defined in ``hargaikan.core.search_decision`` so importing them never
pulls in sentence-transformers or pinecone.
"""

from __future__ import annotations

from hargaikan.core.search_decision import (
    DecisionStatus,
    ScoredMatch,
    SearchBackend,
    SearchResult,
    SearchStatus,
)

__all__ = ["DecisionStatus", "ScoredMatch", "SearchBackend", "SearchResult", "SearchStatus"]
