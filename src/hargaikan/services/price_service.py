"""Price service: reference band, location fallback, verdict and weekly chart data (Req. 6, 10, 12).

Layer 3 (no FastAPI imports). Every band value is "Harga sejarah" and flagged demo when its
band_source contains SYNTHETIC; the user's quote is "Input pengguna". No band → NO_DATA and no
numbers. An invalid quote is reported as INVALID_PRICE text in the view, not as an exception.
The weekly series is read for the location actually used (after fallback).
"""

from __future__ import annotations

from dataclasses import dataclass

from hargaikan.core.location_fallback import BandResolution, UnknownLocationError, resolve_band
from hargaikan.core.text import normalise_name
from hargaikan.core.verdict import InvalidPriceError, Verdict, compute_verdict, parse_price
from hargaikan.data.weekly import WeeklyPoint
from hargaikan.domain import messages as m
from hargaikan.domain.models import Location, PriceBand, Species
from hargaikan.repositories.protocols import PriceRepository
from hargaikan.services.cache import TTLCache
from hargaikan.services.catalog_service import CatalogService
from hargaikan.services.result import NotFoundError, ServiceUnavailable, guarded

SYNTHETIC = "SYNTHETIC"


def band_is_demo(band: PriceBand | None) -> bool:
    return band is not None and SYNTHETIC in band.band_source.upper()


def parse_quote(quote: object | None) -> tuple[float | None, str | None]:
    """(price, error). None / blank → (None, None); invalid → (None, INVALID_PRICE)."""
    if quote is None or (isinstance(quote, str) and not quote.strip()):
        return None, None
    try:
        return parse_price(quote), None
    except InvalidPriceError:
        return None, m.INVALID_PRICE


def clean_grade(grade: str | None) -> str | None:
    if grade is None:
        return None
    g = normalise_name(grade)
    return g or None


@dataclass(frozen=True)
class PriceView:
    species: Species
    requested: Location
    used: Location | None
    is_fallback: bool
    message: str | None  # FALLBACK_TEMPLATE text, or NO_DATA when no band
    band: PriceBand | None
    is_demo: bool
    grade: str | None
    grades: tuple[str, ...]  # grades with a band for this species (any location)
    quote: float | None
    quote_error: str | None  # INVALID_PRICE
    verdict: Verdict | None
    series: tuple[WeeklyPoint, ...] | None  # None when no band or the series is unavailable
    series_notice: str | None  # SECTION_UNAVAILABLE when only the chart failed


class PriceService:
    def __init__(self, catalog: CatalogService, prices: PriceRepository, ttl_s: float | None = 600.0) -> None:
        self._catalog = catalog
        self._prices = prices
        self._bands = TTLCache(ttl_s)
        self._series = TTLCache(ttl_s)

    def bands(self, species_id: str) -> list[PriceBand]:
        """Live bands of one species (cached). Raises ServiceUnavailable("prices")."""
        return self._bands.get(species_id, lambda: guarded("prices", lambda: self._prices.bands_for_species(species_id)))

    def series(self, species_id: str, location_id: str) -> list[WeeklyPoint]:
        return self._series.get(
            (species_id, location_id),
            lambda: guarded("prices", lambda: self._prices.weekly_series(species_id, location_id)),
        )

    def resolve(self, species_id: str, location_id: str, grade: str | None = None) -> BandResolution:
        """Nearest ancestor-or-self band. Raises NotFoundError for an unknown location."""
        snap = self._catalog.snapshot()
        bands = self.bands(species_id)
        try:
            return resolve_band(location_id, species_id, grade, bands, snap.locations_by_id)
        except UnknownLocationError:
            raise NotFoundError(m.UNKNOWN_LOCATION) from None

    def overview(self) -> dict:
        return guarded("overview", self._prices.catalog_overview)

    def reference(
        self, species_id: str, location_id: str, grade: str | None = None, quote: object | None = None
    ) -> PriceView:
        species = self._catalog.species(species_id)
        location = self._catalog.location(location_id)
        g = clean_grade(grade)
        res = self.resolve(species.id, location.id, g)
        grades = tuple(sorted({b.grade for b in self.bands(species.id) if b.grade}))

        price, quote_error = parse_quote(quote)
        verdict = compute_verdict(price, res.band) if price is not None else None

        series: tuple[WeeklyPoint, ...] | None = None
        series_notice = None
        if res.band is not None and res.used is not None:
            try:
                series = tuple(self.series(species.id, res.used.id))
            except ServiceUnavailable as exc:  # only the chart is lost
                series_notice = exc.message

        return PriceView(
            species=species,
            requested=res.requested,
            used=res.used,
            is_fallback=res.is_fallback,
            message=res.message,
            band=res.band,
            is_demo=band_is_demo(res.band),
            grade=g,
            grades=grades,
            quote=price,
            quote_error=quote_error,
            verdict=verdict,
            series=series,
            series_notice=series_notice,
        )
