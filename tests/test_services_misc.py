"""Service-layer helpers: TTL cache, catalog caching, settings for the API, deferred services."""

from __future__ import annotations

from datetime import date

import pytest

from hargaikan import config
from hargaikan.config import Settings, get_env, load_settings
from hargaikan.services.cache import TTLCache
from hargaikan.services.catalog_service import CatalogService
from hargaikan.services.deferred import (
    NullRoutingService,
    NullSeasonalService,
    NullWeatherService,
    RoutingService,
    integration_status,
)
from hargaikan.services.result import ServiceUnavailable
from tests.api_support import ALIASES, LOCATIONS, SP_A, SPECIES
from tests.fakes import FakeCatalogRepository


class _Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def test_ttl_cache_expires_and_does_not_cache_failures() -> None:
    clock = _Clock()
    cache = TTLCache(10, clock=clock)
    calls = []
    assert cache.get("k", lambda: calls.append(1) or "a") == "a"
    assert cache.get("k", lambda: calls.append(1) or "b") == "a"
    clock.t = 11
    assert cache.get("k", lambda: calls.append(1) or "c") == "c"
    with pytest.raises(RuntimeError):
        cache.get("x", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    assert cache.stale("x") is None and len(calls) == 2


class _FlakyCatalog(FakeCatalogRepository):
    fail = False

    def list_species(self, active_only: bool = True):  # type: ignore[no-untyped-def]
        if self.fail:
            raise RuntimeError("down")
        return super().list_species(active_only)


def test_catalog_serves_stale_snapshot_when_refresh_fails() -> None:
    repo = _FlakyCatalog(SPECIES, ALIASES, LOCATIONS)
    svc = CatalogService(repo, ttl_s=0)  # always refresh
    assert svc.species(SP_A.id).code == "IKAN_A"
    repo.fail = True
    assert svc.species(SP_A.id).code == "IKAN_A"  # stale copy
    with pytest.raises(ServiceUnavailable):
        CatalogService(repo, ttl_s=0).snapshot()


def test_exact_index_skips_ambiguous_names() -> None:
    from hargaikan.domain.models import SpeciesAlias

    aliases = (*ALIASES, SpeciesAlias(SPECIES[1].id, "ikan alfa", "local"))
    snap = CatalogService(FakeCatalogRepository(SPECIES, aliases, LOCATIONS)).snapshot()
    assert "ikan alfa" not in snap.exact_index and snap.exact_index["ikan a"] == SP_A.id
    assert snap.kuala_lumpur() is not None


def test_settings_demo_secret_and_env_getter(monkeypatch: pytest.MonkeyPatch) -> None:
    s = load_settings({config.DEMO_SESSION_SECRET: "demo-secret-value"}.get)
    assert s.demo_session_secret == "demo-secret-value" and "demo-secret-value" not in repr(s)
    monkeypatch.setenv("HARGAIKAN_TEST_ENV_ONLY", " ")
    assert get_env("HARGAIKAN_TEST_ENV_ONLY") is None
    monkeypatch.setenv("HARGAIKAN_TEST_ENV_ONLY", "x")
    assert get_env("HARGAIKAN_TEST_ENV_ONLY") == "x"
    assert Settings().catalog_cache_ttl_s == 600.0 and Settings().auth_cache_ttl_s == 60.0


def test_deferred_services_return_nothing() -> None:
    assert NullWeatherService().current("x") is None
    assert NullSeasonalService().events_between(date(2026, 1, 1), date(2026, 12, 31)) == []
    assert NullRoutingService().estimate("a", "b") is None
    assert isinstance(NullRoutingService(), RoutingService)
    status = {x.name: x.connected for x in integration_status(supabase_connected=True, vector_connected=False)}
    assert status == {"supabase": True, "pinecone": False, "weather": False, "seasonal": False, "routing": False, "transit_api": False}


def test_repo_path_uses_working_directory_like_render(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    """``pip install .`` puts the package in site-packages, so the data folder must be found
    relative to the working directory (the repo checkout on Render)."""
    from pathlib import Path

    from hargaikan.data import demo

    root = Path(__file__).resolve().parents[1]
    monkeypatch.setattr(demo, "_REPO_ROOT", tmp_path / "site-packages")  # no data there
    monkeypatch.chdir(root)
    assert demo.repo_path("data/curated/transit_fares.yaml").is_file()
    d = demo.build_demo_dataset(date(2026, 1, 20), weeks=2)
    assert d.profiles and d.similarity and d.locations
