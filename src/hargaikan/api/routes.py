"""Public ``/api/v1`` routes: health, capabilities, catalog, search, prices, recommendations,
forecast, travel, overview and the demo session. Handlers only call services and presenters."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from hargaikan import __version__
from hargaikan.api import presenters as p
from hargaikan.api import schemas as s
from hargaikan.api.auth import AuthUser
from hargaikan.api.container import AppContainer
from hargaikan.api.deps import get_container, optional_user
from hargaikan.data.fares import FareTableError
from hargaikan.domain import messages as m
from hargaikan.domain.models import UserPreferences
from hargaikan.services.deferred import integration_status
from hargaikan.services.result import NotFoundError, ServiceError
from hargaikan.services.travel_service import TravelRequest

ERRORS = {code: {"model": s.ErrorResponse} for code in (400, 404, 422, 503)}

router = APIRouter(responses=ERRORS)

Container = AppContainer


@router.get("/health", response_model=s.HealthOut, tags=["system"])
def health(c: Container = Depends(get_container)) -> s.HealthOut:
    """Liveness only: never calls Supabase (used by the Render health check)."""
    return s.HealthOut(status="ok", backend=c.backend, demo=c.demo, version=__version__)


@router.get("/capabilities", response_model=s.CapabilitiesOut, tags=["system"])
def capabilities(c: Container = Depends(get_container)) -> s.CapabilitiesOut:
    statuses = integration_status(supabase_connected=c.backend == "supabase", vector_connected=c.vector_connected)
    by_name = {x.name: x for x in statuses}
    try:
        entries = c.fare_entries()
        fares = s.FareTableOut(connected=bool(entries), entries=len(entries), note=None)
    except FareTableError:
        fares = s.FareTableOut(connected=False, entries=0, note=m.FARE_TABLE_INVALID)
    return s.CapabilitiesOut(
        backend=c.backend,
        demo=c.demo,
        integrations=[s.IntegrationOut(name=x.name, connected=x.connected, note=x.note) for x in statuses],
        supabase=by_name["supabase"].connected,
        pinecone=by_name["pinecone"].connected,
        weather=by_name["weather"].connected,
        routing=by_name["routing"].connected,
        transit_api=by_name["transit_api"].connected,
        transit_fare_table=fares,
        # The demo dataset never has published predictions; with Supabase it is checked per request.
        forecast_model=s.ForecastModelOut(available=False if c.demo else None, note=m.FORECAST_PUBLISH_NOTE),
    )


@router.get("/species", response_model=s.SpeciesListOut, tags=["catalog"])
def list_species(c: Container = Depends(get_container)) -> s.SpeciesListOut:
    snap = c.services().catalog.snapshot()
    items = [p.species(sp, snap) for sp in snap.species]
    return s.SpeciesListOut(count=len(items), items=items, demo=c.demo)


@router.get("/species/{species_id}", response_model=s.SpeciesOut, tags=["catalog"])
def get_species(species_id: str, c: Container = Depends(get_container)) -> s.SpeciesOut:
    catalog = c.services().catalog
    return p.species(catalog.species(species_id), catalog.snapshot())


@router.get("/locations", response_model=s.LocationListOut, tags=["catalog"])
def list_locations(c: Container = Depends(get_container)) -> s.LocationListOut:
    items = [p.location(loc) for loc in c.services().catalog.locations()]
    return s.LocationListOut(count=len(items), items=items)


@router.get("/search", response_model=s.SearchOut, tags=["catalog"])
def search(q: str = Query(default="", max_length=200), c: Container = Depends(get_container)) -> s.SearchOut:
    return p.search(c.services().search.search(q))


@router.get("/prices/reference", response_model=s.ReferenceOut, tags=["prices"])
def price_reference(
    species_id: str = Query(max_length=64),
    location_id: str = Query(max_length=64),
    grade: str | None = Query(default=None, max_length=64),
    quote: str | None = Query(default=None, max_length=32),
    c: Container = Depends(get_container),
) -> s.ReferenceOut:
    return p.reference(c.services().prices.reference(species_id, location_id, grade, quote))


@router.get("/recommendations", response_model=s.RecommendationsOut, tags=["prices"])
def recommendations(
    species_id: str = Query(max_length=64),
    location_id: str = Query(max_length=64),
    c: Container = Depends(get_container),
    user: AuthUser | None = Depends(optional_user),
) -> s.RecommendationsOut:
    """Public result; with a valid token the caller's allergies and budget are applied."""
    prefs: UserPreferences | None = None
    notice = None
    if user is not None:
        try:
            prefs = c.profile_service(user).preferences()
        except ServiceError:
            notice = m.PROFILE_UNAVAILABLE
    view = c.services().recommendations.for_species(species_id, location_id, prefs)
    return p.recommendations(view, personalised=prefs is not None, notice=notice)


@router.get("/forecast", response_model=s.ForecastOut, tags=["prices"])
def forecast(
    species_id: str = Query(max_length=64),
    location_id: str = Query(max_length=64),
    quote: str | None = Query(default=None, max_length=32),
    c: Container = Depends(get_container),
) -> s.ForecastOut:
    return p.forecast(c.services().forecasts.forecast(species_id, location_id, quote, c.today()))


@router.post("/travel/estimate", response_model=s.TravelOut, tags=["travel"])
def travel_estimate(body: s.TravelIn, c: Container = Depends(get_container)) -> s.TravelOut:
    return p.travel(c.travel.estimate(TravelRequest(**body.model_dump())))


@router.get("/overview", response_model=s.OverviewOut, tags=["catalog"])
def overview(c: Container = Depends(get_container)) -> s.OverviewOut:
    return p.overview(c.services().dashboard.overview())


@router.post("/session/demo", response_model=s.DemoSessionOut, tags=["session"])
def demo_session(c: Container = Depends(get_container)) -> s.DemoSessionOut:
    """Issue a demo access token (memory backend only; 404 otherwise)."""
    if not c.demo:
        raise NotFoundError()
    token, user_id = c.demo_tokens.issue()
    return s.DemoSessionOut(access_token=token, user_id=user_id, notice=m.SESSION_NOTICE)
