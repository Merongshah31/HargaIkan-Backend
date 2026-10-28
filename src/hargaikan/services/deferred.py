"""Deferred outside services (Requirement 25): interfaces plus null implementations.

Layer 3 (no FastAPI imports). Weather (MET Malaysia), seasonal/festival calendars, routing
(OpenStreetMap) and live transit fares are not connected in the MVP. The null implementations
return no data, so nothing from an outside source is ever shown. Their tables
(weather_observations, weather_forecasts, seasonal_events, travel_estimates) exist and stay empty.

To connect one later, implement the Protocol (with ``Settings.service_timeout_s`` on every call
and labelled values) and pass it to the API container; ``integration_status`` then reports it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol, runtime_checkable

from hargaikan.domain import messages as m


@dataclass(frozen=True)
class RouteEstimate:
    distance_km: float
    source: str  # e.g. "OpenStreetMap" + date; shown next to the value


@runtime_checkable
class WeatherService(Protocol):
    def current(self, location_id: str) -> dict | None: ...


@runtime_checkable
class SeasonalService(Protocol):
    def events_between(self, start: date, end: date) -> list: ...


@runtime_checkable
class RoutingService(Protocol):
    def estimate(self, origin_id: str, dest_id: str) -> RouteEstimate | None: ...


class NullWeatherService:
    connected = False

    def current(self, location_id: str) -> None:
        return None


class NullSeasonalService:
    connected = False

    def events_between(self, start: date, end: date) -> list:
        return []


class NullRoutingService:
    connected = False

    def estimate(self, origin_id: str, dest_id: str) -> None:
        return None


@dataclass(frozen=True)
class IntegrationStatus:
    name: str
    connected: bool
    note: str | None


def _connected(service: object) -> bool:
    return bool(getattr(service, "connected", True)) and service is not None


def integration_status(
    *,
    supabase_connected: bool,
    vector_connected: bool,
    weather: object | None = None,
    seasonal: object | None = None,
    routing: object | None = None,
) -> tuple[IntegrationStatus, ...]:
    """Which integrations are connected, with a BM note for each one that is not."""
    weather = weather or NullWeatherService()
    seasonal = seasonal or NullSeasonalService()
    routing = routing or NullRoutingService()
    return (
        IntegrationStatus("supabase", supabase_connected, None if supabase_connected else m.DEMO_DATA),
        IntegrationStatus("pinecone", vector_connected, None if vector_connected else m.VECTOR_SEARCH_NOT_CONNECTED),
        IntegrationStatus("weather", _connected(weather), None if _connected(weather) else m.WEATHER_NOT_CONNECTED),
        IntegrationStatus("seasonal", _connected(seasonal), None if _connected(seasonal) else m.SEASONAL_NOT_CONNECTED),
        IntegrationStatus("routing", _connected(routing), None if _connected(routing) else m.ROUTING_NOT_CONNECTED),
        IntegrationStatus("transit_api", False, m.TRANSIT_API_NOT_CONNECTED),
    )
