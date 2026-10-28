"""Shared helpers for the FastAPI tests: app builders and a small fake catalog."""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi.testclient import TestClient

from hargaikan.api.main import create_app
from hargaikan.config import Settings
from hargaikan.domain.ids import location_id, species_id
from hargaikan.domain.models import Location, PriceBand, SimilarityEdge, Species, SpeciesAlias
from hargaikan.repositories.factory import Repositories
from hargaikan.repositories.memory_repo import MemoryUserRepository, MemoryUserStore
from tests.fakes import FakeCatalogRepository, FakePredictionRepository, FakePriceRepository

API = "/api/v1"

MY = Location(location_id("nasional", "Malaysia"), "Malaysia", "nasional", None, None, None)
KL = Location(location_id("negeri", "Kuala Lumpur"), "Kuala Lumpur", "negeri", MY.id, None, None)
CHOW_KIT = Location(location_id("pasar", "Pasar Chow Kit"), "Pasar Chow Kit", "pasar", KL.id, None, None)
PUDU = Location(location_id("pasar", "Pasar Pudu"), "Pasar Pudu", "pasar", KL.id, 3.13, 101.71)
LOCATIONS = (MY, KL, CHOW_KIT, PUDU)

SP_A = Species(species_id("IKAN_A"), "IKAN_A", "IKAN A", "Fish A", (), True)
SP_B = Species(species_id("IKAN_B"), "IKAN_B", "IKAN B", None, (), True)
SP_OLD = Species(species_id("IKAN_LAMA"), "IKAN_LAMA", "IKAN LAMA", None, (), False)
SPECIES = (SP_A, SP_B, SP_OLD)
ALIASES = (SpeciesAlias(SP_A.id, "ikan alfa", "local"), SpeciesAlias(SP_OLD.id, "ikan lama", "raw"))

P1, P2 = date(2026, 1, 5), date(2026, 1, 18)


def band(sp: Species, loc: Location, p25: float, med: float, p75: float, n: int, source: str = "IKANSIGHT", grade: str | None = None) -> PriceBand:
    conf = "tinggi" if n >= 10 else "sederhana" if n >= 3 else "rendah"
    return PriceBand(sp.id, loc.id, grade, p25, med, p75, n, conf, P1, P2, source)  # type: ignore[arg-type]


BANDS = (
    band(SP_A, PUDU, 18.0, 20.0, 22.0, 12),
    band(SP_A, KL, 19.0, 21.0, 23.0, 30),
    band(SP_B, PUDU, 13.0, 15.0, 17.0, 5),
    band(SP_B, KL, 14.0, 16.0, 18.0, 20),
)
EDGES = (SimilarityEdge(SP_A.id, SP_B.id, 0.8, "Isi putih dan rasa yang serupa.", ("tekstur",), True),)
SERIES = {(SP_A.id, PUDU.id): [(date(2026, 1, 5), 19.5, 6), (date(2026, 1, 12), 20.5, 6)]}


def fake_repositories(
    *,
    catalog: Any = None,
    prices: Any = None,
    predictions: Any = None,
    backend: str = "supabase",
) -> Repositories:
    return Repositories(
        catalog if catalog is not None else FakeCatalogRepository(SPECIES, ALIASES, LOCATIONS, (), EDGES),
        prices if prices is not None else FakePriceRepository(BANDS, SERIES, {"species_count": 2, "premise_count": 2}),
        predictions if predictions is not None else FakePredictionRepository(),
        backend,  # type: ignore[arg-type]
    )


def store_factory(store: MemoryUserStore):  # type: ignore[no-untyped-def]
    return lambda user: MemoryUserRepository(store, user.user_id)


def memory_client(store: MemoryUserStore | None = None, **kwargs: Any) -> TestClient:
    store = store if store is not None else MemoryUserStore()
    settings = kwargs.pop("settings", None) or Settings(data_backend="memory")
    return TestClient(create_app(settings, user_repo_factory=store_factory(store), **kwargs))


def fake_client(repos: Repositories | None = None, store: MemoryUserStore | None = None, **kwargs: Any) -> TestClient:
    store = store if store is not None else MemoryUserStore()
    settings = kwargs.pop("settings", None) or Settings(data_backend="memory")
    return TestClient(create_app(settings, repos or fake_repositories(), user_repo_factory=store_factory(store), **kwargs))


def demo_headers(client: TestClient) -> dict[str, str]:
    resp = client.post(f"{API}/session/demo")
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def ids_by_name(client: TestClient) -> tuple[dict[str, str], dict[str, str]]:
    """(species name → id, location name → id) from the API."""
    species = {x["name_ms"]: x["id"] for x in client.get(f"{API}/species").json()["items"]}
    locations = {x["name"]: x["id"] for x in client.get(f"{API}/locations").json()["items"]}
    return species, locations


def all_strings(obj: Any) -> list[str]:
    if isinstance(obj, str):
        return [obj]
    if isinstance(obj, dict):
        return [s for v in obj.values() for s in all_strings(v)]
    if isinstance(obj, list):
        return [s for v in obj for s in all_strings(v)]
    return []
