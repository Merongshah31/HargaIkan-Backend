"""Forecast service: published model estimates with their limits (Requirement 20).

Layer 3 (no FastAPI imports). Reads only published predictions (cached with
``Settings.prediction_cache_ttl_s``) and applies :func:`hargaikan.core.forecast_display.forecast_view`
against the band at the resolved location. The prediction is looked up at the requested
location first, then at the location the band fell back to. Nothing is trained or estimated
here; without a visible prediction the view carries FORECAST_UNAVAILABLE and no numbers.
Travel data never reaches this service.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from hargaikan.config import Settings
from hargaikan.core.forecast_display import ForecastView, forecast_view
from hargaikan.domain.models import Location, PricePrediction, Species
from hargaikan.repositories.protocols import PredictionRepository
from hargaikan.services.cache import TTLCache
from hargaikan.services.catalog_service import CatalogService
from hargaikan.services.price_service import PriceService, band_is_demo, parse_quote
from hargaikan.services.result import guarded


@dataclass(frozen=True)
class ForecastResult:
    species: Species
    requested: Location
    prediction_location: Location | None  # where the shown prediction was published
    view: ForecastView
    quote: float | None
    quote_error: str | None
    demo: bool


class ForecastService:
    def __init__(
        self,
        catalog: CatalogService,
        prices: PriceService,
        predictions: PredictionRepository,
        settings: Settings,
        ttl_s: float | None = 600.0,
        demo_backend: bool = False,
    ) -> None:
        self._catalog = catalog
        self._prices = prices
        self._predictions = predictions
        self._s = settings
        self._cache = TTLCache(ttl_s)
        self._demo_backend = demo_backend

    def _prediction(self, species_id: str, location_id: str) -> PricePrediction | None:
        return self._cache.get(
            (species_id, location_id),
            lambda: guarded("forecast", lambda: self._predictions.live_prediction(species_id, location_id)),
        )

    def forecast(self, species_id: str, location_id: str, quote: object | None, today: date) -> ForecastResult:
        species = self._catalog.species(species_id)
        location = self._catalog.location(location_id)
        res = self._prices.resolve(species.id, location.id, None)
        price, quote_error = parse_quote(quote)

        candidates = [location] + ([res.used] if res.used is not None and res.used.id != location.id else [])
        chosen: tuple[Location, PricePrediction] | None = None
        for loc in candidates:
            p = self._prediction(species.id, loc.id)
            if p is not None and forecast_view(p, res.band, price, today, self._s).available:
                chosen = (loc, p)
                break
        view = forecast_view(chosen[1] if chosen else None, res.band, price, today, self._s)
        return ForecastResult(
            species=species,
            requested=location,
            prediction_location=chosen[0] if chosen and view.available else None,
            view=view,
            quote=price,
            quote_error=quote_error,
            demo=self._demo_backend or band_is_demo(res.band),
        )
