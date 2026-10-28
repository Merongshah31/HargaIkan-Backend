"""Dashboard service: personal overview of preferred fish, or the empty-profile state (Req. 17).

Layer 3 (no FastAPI imports). Per preferred species: the reference band at the preferred
location (or the Kuala Lumpur negeri location when none is stored), recommendations, a forecast
summary and a budget comparison (median vs budget). Each part of each item is isolated: a
failing species/section gets SECTION_UNAVAILABLE and the rest still renders.

Empty profile (no preferred species): EMPTY_PROFILE, the catalog overview and general
suggestions (species with the most price records at Kuala Lumpur, "Harga sejarah").
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

from hargaikan.domain import messages as m
from hargaikan.domain.models import Location, PriceBand, Species, UserPreferences
from hargaikan.services.catalog_service import CatalogService
from hargaikan.services.forecast_service import ForecastResult, ForecastService
from hargaikan.services.price_service import PriceService, PriceView, band_is_demo
from hargaikan.services.recommendation_service import RecommendationService, RecommendationView
from hargaikan.services.result import NotFoundError, ServiceError

logger = logging.getLogger(__name__)

MAX_SUGGESTIONS = 5


@dataclass(frozen=True)
class BudgetComparison:
    budget: float  # "Input pengguna"
    median: float  # "Harga sejarah"
    within: bool
    text: str  # WITHIN_BUDGET or OVER_BUDGET


@dataclass(frozen=True)
class DashboardItem:
    species: Species
    reference: PriceView | None
    reference_notice: str | None
    recommendations: RecommendationView | None
    recommendations_notice: str | None
    forecast: ForecastResult | None
    forecast_notice: str | None
    budget: BudgetComparison | None


@dataclass(frozen=True)
class Suggestion:
    species: Species
    location: Location
    band: PriceBand
    is_demo: bool


@dataclass(frozen=True)
class Overview:
    species_count: int | None
    premise_count: int | None
    latest_period: date | None
    demo: bool
    notice: str | None  # SECTION_UNAVAILABLE when the overview failed
    suggestions: tuple[Suggestion, ...]
    suggestions_notice: str | None


@dataclass(frozen=True)
class Dashboard:
    state: str  # "empty" | "personalised"
    message: str | None  # EMPTY_PROFILE
    location: Location | None
    location_note: str | None  # DASHBOARD_DEFAULT_LOCATION when KL is used by default
    items: tuple[DashboardItem, ...]
    overview: Overview | None


def compare_budget(band: PriceBand | None, budget: float | None) -> BudgetComparison | None:
    if band is None or budget is None:
        return None
    within = band.median <= budget
    return BudgetComparison(budget, band.median, within, m.WITHIN_BUDGET if within else m.OVER_BUDGET)


class DashboardService:
    def __init__(
        self,
        catalog: CatalogService,
        prices: PriceService,
        recommendations: RecommendationService,
        forecasts: ForecastService,
        demo_backend: bool = False,
    ) -> None:
        self._catalog = catalog
        self._prices = prices
        self._recs = recommendations
        self._forecasts = forecasts
        self._demo_backend = demo_backend

    # -- public overview ------------------------------------------------------------------------

    def suggestions(self) -> tuple[Suggestion, ...]:
        """Active species with the most price records at Kuala Lumpur (all grades)."""
        snap = self._catalog.snapshot()
        kl = snap.kuala_lumpur()
        if kl is None:
            return ()
        out: list[Suggestion] = []
        for sp in snap.species:
            res = self._prices.resolve(sp.id, kl.id, None)
            if res.band is not None and res.used is not None:
                out.append(Suggestion(sp, res.used, res.band, band_is_demo(res.band)))
        out.sort(key=lambda s: (-s.band.record_count, s.species.canonical_name_ms))
        return tuple(out[:MAX_SUGGESTIONS])

    def overview(self) -> Overview:
        notice = None
        data: dict = {}
        try:
            data = self._prices.overview()
        except ServiceError as exc:
            notice = exc.messages[0]
        sugg_notice = None
        suggestions: tuple[Suggestion, ...] = ()
        try:
            suggestions = self.suggestions()
        except ServiceError as exc:
            sugg_notice = exc.messages[0]
        return Overview(
            species_count=data.get("species_count"),
            premise_count=data.get("premise_count"),
            latest_period=data.get("latest_period"),
            demo=bool(data.get("demo")) or self._demo_backend,
            notice=notice,
            suggestions=suggestions,
            suggestions_notice=sugg_notice,
        )

    # -- personal dashboard ---------------------------------------------------------------------

    def _item(self, sp: Species, location: Location, prefs: UserPreferences, today: date) -> DashboardItem:
        ref = ref_notice = recs = recs_notice = fc = fc_notice = None
        try:
            ref = self._prices.reference(sp.id, location.id)
        except ServiceError as exc:
            ref_notice = m.SECTION_UNAVAILABLE if not isinstance(exc, NotFoundError) else exc.messages[0]
        except Exception:  # isolate unexpected failures to this section
            logger.exception("dashboard reference failed")
            ref_notice = m.SECTION_UNAVAILABLE
        try:
            recs = self._recs.for_species(sp.id, location.id, prefs)
        except Exception:
            recs_notice = m.SECTION_UNAVAILABLE
        try:
            fc = self._forecasts.forecast(sp.id, location.id, None, today)
        except Exception:
            fc_notice = m.SECTION_UNAVAILABLE
        budget = compare_budget(ref.band if ref else None, prefs.budget_rm_per_kg)
        return DashboardItem(sp, ref, ref_notice, recs, recs_notice, fc, fc_notice, budget)

    def for_user(self, prefs: UserPreferences | None, today: date) -> Dashboard:
        if prefs is None or not prefs.preferred_species_ids:
            return Dashboard("empty", m.EMPTY_PROFILE, None, None, (), self.overview())
        snap = self._catalog.snapshot()
        note = None
        location = snap.locations_by_id.get(prefs.preferred_location_id or "")
        if location is None:
            location = snap.kuala_lumpur()
            note = m.DASHBOARD_DEFAULT_LOCATION
        if location is None:
            return Dashboard("empty", m.EMPTY_PROFILE, None, None, (), self.overview())
        items = []
        for sid in prefs.preferred_species_ids:
            sp = snap.species_by_id.get(sid)
            if sp is None:  # no longer active in the catalog
                continue
            items.append(self._item(sp, location, prefs, today))
        return Dashboard("personalised", None, location, note, tuple(items), None)
