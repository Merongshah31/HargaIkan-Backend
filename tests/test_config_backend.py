"""Backend-related settings: DATA_BACKEND, CORS_ORIGINS, SUPABASE_JWT_SECRET, fare table path."""

from __future__ import annotations

import pytest

from hargaikan import config
from hargaikan.config import DEFAULT_CORS_ORIGINS, Settings, load_settings, parse_cors_origins, resolved_data_backend


def test_defaults() -> None:
    s = Settings()
    assert s.data_backend is None
    assert s.cors_origins == ("http://localhost:5173", "http://localhost:3000") == DEFAULT_CORS_ORIGINS
    assert s.supabase_jwt_secret is None
    assert s.transit_fare_table_path == "data/curated/transit_fares.yaml"
    assert load_settings(lambda name: None) == Settings()


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({}, "memory"),
        ({"supabase_url": "https://x.supabase.co"}, "memory"),
        ({"supabase_anon_key": "k"}, "memory"),
        ({"supabase_url": "https://x.supabase.co", "supabase_anon_key": "k"}, "supabase"),
        ({"supabase_url": "https://x.supabase.co", "supabase_anon_key": "k", "data_backend": "memory"}, "memory"),
        ({"data_backend": "supabase"}, "supabase"),
    ],
)
def test_resolved_data_backend(kwargs: dict, expected: str) -> None:
    assert resolved_data_backend(Settings(**kwargs)) == expected


def test_load_settings_reads_backend_settings() -> None:
    values = {
        config.DATA_BACKEND: " Supabase ",
        config.CORS_ORIGINS: "https://hargaikan.vercel.app/, http://localhost:5173,,https://hargaikan.vercel.app",
        config.SUPABASE_JWT_SECRET: "jwt-value",
        config.TRANSIT_FARE_TABLE_PATH: "other/fares.yaml",
    }
    s = load_settings(values.get)
    assert s.data_backend == "supabase"
    assert s.cors_origins == ("https://hargaikan.vercel.app", "http://localhost:5173")
    assert s.supabase_jwt_secret == "jwt-value"
    assert s.transit_fare_table_path == "other/fares.yaml"


def test_invalid_data_backend_is_rejected_without_echoing_value() -> None:
    with pytest.raises(ValueError) as exc:
        load_settings({config.DATA_BACKEND: "postgres-xyz"}.get)
    assert "DATA_BACKEND" in str(exc.value) and "postgres-xyz" not in str(exc.value)


@pytest.mark.parametrize("raw", [None, "", " , ,"])
def test_cors_defaults_when_blank(raw: str | None) -> None:
    assert parse_cors_origins(raw) == DEFAULT_CORS_ORIGINS


def test_jwt_secret_is_masked_in_repr() -> None:
    s = Settings(supabase_jwt_secret="very-secret-jwt")
    assert "very-secret-jwt" not in repr(s) and "supabase_jwt_secret='***'" in repr(s)
