"""rapidfuzz fallback search over the Supabase species catalog (Requirement 11.8).

Used when Pinecone or the embedding model fails. Only active species are searched, and names
always come from the catalog passed in (Supabase), never from index metadata.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from rapidfuzz import fuzz

from hargaikan.core.search_decision import ScoredMatch, best_per_species
from hargaikan.core.text import normalise_term
from hargaikan.domain.models import Species, SpeciesAlias


def _names_by_species(species: Sequence[Species], aliases: Iterable[SpeciesAlias]) -> dict[str, list[str]]:
    names: dict[str, list[str]] = {}
    for sp in species:
        if not sp.active:
            continue
        own = names.setdefault(sp.id, [])
        own.append(sp.canonical_name_ms)
        if sp.canonical_name_en:
            own.append(sp.canonical_name_en)
    for a in aliases:
        if a.species_id in names:  # skips aliases of inactive or unknown species
            names[a.species_id].append(a.alias)
    return names


def fuzzy_search(
    query: str,
    species: Sequence[Species],
    aliases: Iterable[SpeciesAlias],
    limit: int = 10,
) -> list[ScoredMatch]:
    """Score ``query`` against canonical names and aliases of active species with ``WRatio / 100``.

    Both sides are compared after ``normalise_term``. Returns the best match per species, sorted by
    score descending (ties by name, then id), at most ``limit`` items. A blank query returns [].
    """
    q = normalise_term(query)
    if not q or limit <= 0:
        return []
    by_id = {sp.id: sp for sp in species if sp.active}
    matches: list[ScoredMatch] = []
    for sid, names in _names_by_species(species, aliases).items():
        name_ms = by_id[sid].canonical_name_ms
        for name in names:
            target = normalise_term(name)
            if not target:
                continue
            score = fuzz.WRatio(q, target, processor=None) / 100.0
            matches.append(ScoredMatch(sid, name_ms, name, min(1.0, max(0.0, score))))
    return best_per_species(matches)[:limit]
