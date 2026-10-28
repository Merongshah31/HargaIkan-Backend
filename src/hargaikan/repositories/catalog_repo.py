"""Supabase :class:`~hargaikan.repositories.protocols.CatalogRepository` (anon key, read only)."""

from __future__ import annotations

from typing import Any

from hargaikan.domain.models import FishProfile, Location, SimilarityEdge, Species, SpeciesAlias
from hargaikan.repositories.rows import (
    ALIAS_COLUMNS,
    LOCATION_COLUMNS,
    PROFILE_COLUMNS,
    SIMILARITY_COLUMNS,
    SPECIES_COLUMNS,
    alias_from_row,
    fetch_all,
    location_from_row,
    profile_from_row,
    similarity_from_row,
    species_from_row,
)


class SupabaseCatalogRepository:
    """Catalog reads over an anon-key supabase Client (public select policies)."""

    def __init__(self, client: Any) -> None:
        self.client = client

    def list_species(self, active_only: bool = True) -> list[Species]:
        def build() -> Any:
            q = self.client.table("species").select(SPECIES_COLUMNS)
            if active_only:
                q = q.eq("active", True)
            return q.order("code")

        return [species_from_row(r) for r in fetch_all(build)]

    def list_aliases(self) -> list[SpeciesAlias]:
        rows = fetch_all(lambda: self.client.table("species_aliases").select(ALIAS_COLUMNS).order("id"))
        return [alias_from_row(r) for r in rows]

    def list_locations(self) -> list[Location]:
        rows = fetch_all(lambda: self.client.table("locations").select(LOCATION_COLUMNS).order("id"))
        return [location_from_row(r) for r in rows]

    def list_profiles(self) -> list[FishProfile]:
        """fish_profiles is keyed by species_id; FishProfile carries the species code, so the
        species table (active and inactive) is read to map ids to codes. Rows whose species is
        unknown are skipped."""
        code_by_id = {sp.id: sp.code for sp in self.list_species(active_only=False)}
        rows = fetch_all(lambda: self.client.table("fish_profiles").select(PROFILE_COLUMNS).order("species_id"))
        return [p for r in rows if (p := profile_from_row(r, code_by_id)) is not None]

    def list_similarity(self, from_species_id: str) -> list[SimilarityEdge]:
        """All edges from the species, verified or not; the recommender uses verified ones only."""
        rows = fetch_all(
            lambda: self.client.table("fish_similarity")
            .select(SIMILARITY_COLUMNS)
            .eq("from_species_id", str(from_species_id))
            .order("to_species_id")
        )
        return [similarity_from_row(r) for r in rows]
