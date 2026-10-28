"""Per-app wiring: settings, repositories (built lazily), services and authentication.

Nothing here touches the network at construction time. Repositories are built on first use;
if that fails (e.g. Supabase misconfigured) the affected request gets 503 SECTION_UNAVAILABLE
and the next request tries again. Travel estimates do not depend on the repositories.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

import httpx

from hargaikan.api.auth import Authenticator, AuthUser, DemoTokens, SupabaseTokenVerifier
from hargaikan.config import DataBackend, Settings, resolved_data_backend
from hargaikan.data.fares import FareEntry
from hargaikan.repositories.factory import Repositories, build_repositories, fare_table, user_repository_for
from hargaikan.repositories.protocols import UserRepository
from hargaikan.repositories.supabase_client import safe_error
from hargaikan.services.catalog_service import CatalogService
from hargaikan.services.dashboard_service import DashboardService
from hargaikan.services.forecast_service import ForecastService
from hargaikan.services.price_service import PriceService
from hargaikan.services.profile_service import ProfileService
from hargaikan.services.recommendation_service import RecommendationService
from hargaikan.services.result import ServiceUnavailable
from hargaikan.services.search_service import SearchService, VectorSearch
from hargaikan.services.travel_service import TravelService

logger = logging.getLogger(__name__)

UserRepoFactory = Callable[[AuthUser], UserRepository]


@dataclass(frozen=True)
class Services:
    repositories: Repositories
    catalog: CatalogService
    prices: PriceService
    search: SearchService
    recommendations: RecommendationService
    forecasts: ForecastService
    dashboard: DashboardService


class AppContainer:
    def __init__(
        self,
        settings: Settings,
        repositories: Repositories | None = None,
        *,
        user_repo_factory: UserRepoFactory | None = None,
        vector_search: VectorSearch | None = None,
        fare_entries: list[FareEntry] | None = None,
        http_client: httpx.Client | None = None,
        today: Callable[[], date] = date.today,
    ) -> None:
        self.settings = settings
        self.backend: DataBackend = repositories.backend if repositories is not None else resolved_data_backend(settings)
        self._repos = repositories
        self._services: Services | None = None
        self._lock = threading.Lock()
        self._vector = vector_search
        self._fares = fare_entries
        self._user_repo_factory = user_repo_factory
        self.today = today
        self.demo_tokens = DemoTokens(settings.demo_session_secret)
        self.authenticator = Authenticator(self.backend, self.demo_tokens, SupabaseTokenVerifier(settings, http_client))
        self.travel = TravelService(self.fare_entries)

    @property
    def demo(self) -> bool:
        return self.backend == "memory"

    def services(self) -> Services:
        if self._services is not None:
            return self._services
        with self._lock:
            if self._services is None:
                try:
                    repos = self._repos if self._repos is not None else build_repositories(self.settings)
                except Exception as exc:
                    logger.warning("repositories unavailable: %s", safe_error(exc))
                    raise ServiceUnavailable("data") from exc
                ttl = None if repos.backend == "memory" else self.settings.catalog_cache_ttl_s
                pred_ttl = None if repos.backend == "memory" else self.settings.prediction_cache_ttl_s
                catalog = CatalogService(repos.catalog, ttl)
                prices = PriceService(catalog, repos.prices, ttl)
                recs = RecommendationService(catalog, prices, self.settings)
                forecasts = ForecastService(
                    catalog, prices, repos.predictions, self.settings, pred_ttl, demo_backend=repos.demo
                )
                self._services = Services(
                    repositories=repos,
                    catalog=catalog,
                    prices=prices,
                    search=SearchService(catalog, self.settings, self._vector),
                    recommendations=recs,
                    forecasts=forecasts,
                    dashboard=DashboardService(catalog, prices, recs, forecasts, demo_backend=repos.demo),
                )
        return self._services

    @property
    def vector_connected(self) -> bool:
        return self._vector is not None

    def fare_entries(self) -> list[FareEntry]:
        """The maintained fare table (loaded once). Raises FareTableError for an invalid file."""
        if self._fares is None:
            self._fares = fare_table(self.settings)
        return self._fares

    def user_repository(self, user: AuthUser) -> UserRepository:
        if self._user_repo_factory is not None:
            return self._user_repo_factory(user)
        try:
            return user_repository_for(self.settings, user.user_id, user.access_token)
        except Exception as exc:
            logger.warning("user repository unavailable: %s", safe_error(exc))
            raise ServiceUnavailable("profile") from exc

    def profile_service(self, user: AuthUser) -> ProfileService:
        return ProfileService(self.user_repository(user), user.user_id, self.services().catalog, self.settings)
