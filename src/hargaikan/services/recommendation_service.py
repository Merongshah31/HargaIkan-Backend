"""Recommendation service: verified cheaper similar fish at the same selected location (Req. 16).

Layer 3 (no FastAPI imports). Wraps :func:`hargaikan.core.recommender.recommend`; every band is
resolved from the same selected location as the original species. The disclaimer is always
returned, and ALLERGY_CAUTION when the original species matches the user's allergy data.
"""

from __future__ import annotations

from dataclasses import dataclass

from hargaikan.config import Settings
from hargaikan.core.location_fallback import BandResolution
from hargaikan.core.recommender import RecommendationResult, recommend
from hargaikan.domain import messages as m
from hargaikan.domain.models import Location, Species, UserPreferences
from hargaikan.services.catalog_service import CatalogService
from hargaikan.services.price_service import PriceService


@dataclass(frozen=True)
class RecommendationView:
    species: Species
    location: Location
    result: RecommendationResult
    allergy_caution: str | None  # ALLERGY_CAUTION when result.original_allergy_match
    disclaimer: str  # always RECOMMENDATION_DISCLAIMER


class RecommendationService:
    def __init__(self, catalog: CatalogService, prices: PriceService, settings: Settings) -> None:
        self._catalog = catalog
        self._prices = prices
        self._s = settings

    def for_species(self, species_id: str, location_id: str, prefs: UserPreferences | None) -> RecommendationView:
        species = self._catalog.species(species_id)
        location = self._catalog.location(location_id)
        snap = self._catalog.snapshot()
        edges = self._catalog.similarity(species.id)

        def resolve(sid: str) -> BandResolution:
            return self._prices.resolve(sid, location.id, None)

        result = recommend(
            species, prefs, snap.species_by_id, snap.aliases, edges, snap.profiles_by_code, resolve, self._s
        )
        caution = m.ALLERGY_CAUTION if result.original_allergy_match else None
        return RecommendationView(species, location, result, caution, result.disclaimer or m.RECOMMENDATION_DISCLAIMER)
