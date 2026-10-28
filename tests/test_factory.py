"""Repository factory: backend auto-selection, memory caching, per-user repositories, fare table."""

from __future__ import annotations

from pathlib import Path

import pytest

from hargaikan.config import Settings
from hargaikan.repositories import factory
from hargaikan.repositories.catalog_repo import SupabaseCatalogRepository
from hargaikan.repositories.memory_repo import MemoryCatalogRepository, MemoryUserRepository
from hargaikan.repositories.supabase_client import MissingSecretError
from hargaikan.repositories.user_repo import SupabaseUserRepository
from tests.supabase_fakes import FakeClient

CREDS = {"supabase_url": "https://example.supabase.co", "supabase_anon_key": "anon-placeholder"}


def test_auto_selects_memory_without_credentials() -> None:
    repos = factory.build_repositories(Settings())
    assert repos.backend == "memory" and repos.demo
    assert isinstance(repos.catalog, MemoryCatalogRepository)
    assert repos.prices.catalog_overview()["demo"] is True


def test_memory_dataset_is_built_once() -> None:
    a = factory.build_repositories(Settings())
    b = factory.build_repositories(Settings())
    assert factory.demo_dataset() is factory.demo_dataset()
    assert a.catalog.list_species() == b.catalog.list_species()


def test_auto_selects_supabase_with_credentials() -> None:
    client = FakeClient()
    repos = factory.build_repositories(Settings(**CREDS), client=client)
    assert repos.backend == "supabase" and not repos.demo
    assert isinstance(repos.catalog, SupabaseCatalogRepository) and repos.catalog.client is client


def test_auto_supabase_builds_anon_client(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = {}

    def fake_anon(settings: Settings) -> FakeClient:
        seen["key"] = settings.supabase_anon_key
        return FakeClient()

    monkeypatch.setattr("hargaikan.repositories.supabase_client.anon_client", fake_anon)
    repos = factory.build_repositories(Settings(**CREDS, supabase_service_role_key="service-placeholder"))
    assert repos.backend == "supabase" and seen == {"key": "anon-placeholder"}


def test_explicit_memory_wins_over_credentials() -> None:
    assert factory.build_repositories(Settings(**CREDS, data_backend="memory")).backend == "memory"


def test_explicit_supabase_without_credentials_names_missing_keys() -> None:
    with pytest.raises(MissingSecretError) as exc:
        factory.build_repositories(Settings(data_backend="supabase"))
    assert set(exc.value.names) == {"SUPABASE_URL", "SUPABASE_ANON_KEY"}


def test_user_repository_for_memory_is_bound_to_user() -> None:
    repo = factory.user_repository_for(Settings(), "demo-user", None)
    assert isinstance(repo, MemoryUserRepository) and repo.user_id == "demo-user"


def test_user_repository_for_supabase_uses_token() -> None:
    client = FakeClient()
    repo = factory.user_repository_for(Settings(**CREDS), "u1", "tok", client=client)
    assert isinstance(repo, SupabaseUserRepository) and client.postgrest.auth_calls == ["tok"]
    with pytest.raises(ValueError):
        factory.user_repository_for(Settings(**CREDS), "u1", None, client=client)


def test_user_repository_for_supabase_never_uses_service_role(monkeypatch: pytest.MonkeyPatch) -> None:
    keys = []
    monkeypatch.setattr(
        "hargaikan.repositories.supabase_client._create", lambda url, key: keys.append(key) or FakeClient()
    )
    factory.user_repository_for(Settings(**CREDS, supabase_service_role_key="service-placeholder"), "u1", "tok")
    assert keys == ["anon-placeholder"]


def test_fare_table_default_is_empty() -> None:
    assert factory.fare_table(Settings()) == []


def test_fare_table_missing_file_is_empty(tmp_path: Path) -> None:
    assert factory.fare_table(Settings(transit_fare_table_path=str(tmp_path / "none.yaml"))) == []
