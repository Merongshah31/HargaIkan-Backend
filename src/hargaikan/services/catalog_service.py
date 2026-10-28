"""Catalog service: active species (with aliases and profiles) and the location hierarchy.

Layer 3 (no FastAPI imports). The catalog is read through :class:`CatalogRepository` and cached
in-process with a TTL (``Settings.catalog_cache_ttl_s``; ``None`` = forever for the memory
backend). If a refresh fails while an older snapshot exists, the older snapshot is served
(public catalog data only). Similarity edges are cached per species with the same TTL.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field

from hargaikan.core.text import normalise_term
from hargaikan.domain import messages as m
from hargaikan.domain.models import FishProfile, Location, SimilarityEdge, Species, SpeciesAlias
from hargaikan.repositories.protocols import CatalogRepository
from hargaikan.repositories.supabase_client import safe_error
from hargaikan.services.cache import TTLCache
from hargaikan.services.result import NotFoundError, ServiceUnavailable, guarded

logger = logging.getLogger(__name__)

KUALA_LUMPUR = "Kuala Lumpur"


@dataclass(frozen=True)
class CatalogSnapshot:
    species: tuple[Species, ...]  # active only
    aliases: tuple[SpeciesAlias, ...]  # aliases of active species only
    locations: tuple[Location, ...]
    profiles: tuple[FishProfile, ...]
    species_by_id: Mapping[str, Species] = field(default_factory=dict)
    locations_by_id: Mapping[str, Location] = field(default_factory=dict)
    profiles_by_code: Mapping[str, FishProfile] = field(default_factory=dict)
    aliases_by_species: Mapping[str, tuple[SpeciesAlias, ...]] = field(default_factory=dict)
    exact_index: Mapping[str, str] = field(default_factory=dict)  # normalised name/alias → species id

    @classmethod
    def build(
        cls,
        species: list[Species],
        aliases: list[SpeciesAlias],
        locations: list[Location],
        profiles: list[FishProfile],
    ) -> CatalogSnapshot:
        active = tuple(sorted((s for s in species if s.active), key=lambda s: (s.canonical_name_ms, s.id)))
        by_id = {s.id: s for s in active}
        kept = tuple(a for a in aliases if a.species_id in by_id)
        per_species: dict[str, list[SpeciesAlias]] = {}
        for a in kept:
            per_species.setdefault(a.species_id, []).append(a)

        # Exact index: a normalised name that points to more than one species is ambiguous and
        # left to the scored search instead.
        owners: dict[str, set[str]] = {}
        for sp in active:
            for name in (sp.canonical_name_ms, sp.canonical_name_en):
                if name and (key := normalise_term(name)):
                    owners.setdefault(key, set()).add(sp.id)
        for a in kept:
            if key := normalise_term(a.alias):
                owners.setdefault(key, set()).add(a.species_id)
        exact = {k: next(iter(v)) for k, v in owners.items() if len(v) == 1}

        return cls(
            species=active,
            aliases=kept,
            locations=tuple(locations),
            profiles=tuple(profiles),
            species_by_id=by_id,
            locations_by_id={loc.id: loc for loc in locations},
            profiles_by_code={p.species_code: p for p in profiles},
            aliases_by_species={k: tuple(v) for k, v in per_species.items()},
            exact_index=exact,
        )

    def kuala_lumpur(self) -> Location | None:
        """The negeri-level Kuala Lumpur location (Kuala_Lumpur_Reference), if present."""
        for loc in self.locations:
            if loc.level == "negeri" and loc.name == KUALA_LUMPUR:
                return loc
        return None


class CatalogService:
    def __init__(self, repo: CatalogRepository, ttl_s: float | None = 600.0) -> None:
        self._repo = repo
        self._snapshot_cache = TTLCache(ttl_s, max_entries=1)
        self._edge_cache = TTLCache(ttl_s)

    def _load(self) -> CatalogSnapshot:
        return CatalogSnapshot.build(
            self._repo.list_species(active_only=True),
            self._repo.list_aliases(),
            self._repo.list_locations(),
            self._repo.list_profiles(),
        )

    def snapshot(self) -> CatalogSnapshot:
        """The cached catalog. Raises ServiceUnavailable("catalog") if it cannot be loaded."""
        try:
            return self._snapshot_cache.get("catalog", self._load)
        except Exception as exc:
            stale = self._snapshot_cache.stale("catalog")
            if stale is not None:
                logger.warning("catalog refresh failed, serving cached copy: %s", safe_error(exc))
                return stale
            logger.warning("catalog unavailable: %s", safe_error(exc))
            raise ServiceUnavailable("catalog") from exc

    def species_list(self) -> tuple[Species, ...]:
        return self.snapshot().species

    def species(self, species_id: str) -> Species:
        sp = self.snapshot().species_by_id.get(str(species_id).strip())
        if sp is None:
            raise NotFoundError(m.UNKNOWN_SPECIES)
        return sp

    def location(self, location_id: str) -> Location:
        loc = self.snapshot().locations_by_id.get(str(location_id).strip())
        if loc is None:
            raise NotFoundError(m.UNKNOWN_LOCATION)
        return loc

    def locations(self) -> tuple[Location, ...]:
        return self.snapshot().locations

    def similarity(self, species_id: str) -> list[SimilarityEdge]:
        """Curated similarity edges from ``species_id`` (verified and draft; the recommender filters)."""
        return self._edge_cache.get(
            ("edges", species_id), lambda: guarded("recommendations", lambda: self._repo.list_similarity(species_id))
        )
