"""Unit tests for hargaikan.config (secret resolution and settings)."""

from __future__ import annotations

import logging
from dataclasses import FrozenInstanceError

import pytest
import streamlit

from hargaikan import config
from hargaikan.config import Settings, get_secret, load_settings

KEY = "HARGAIKAN_TEST_SECRET"


class _MissingSecretsFile:
    """Behaves like st.secrets when no secrets.toml exists: any access raises."""

    def __contains__(self, name: object) -> bool:
        raise FileNotFoundError("No secrets files found.")

    def __getitem__(self, name: str) -> str:
        raise FileNotFoundError("No secrets files found.")


@pytest.fixture
def no_st_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(streamlit, "secrets", _MissingSecretsFile())


def test_st_secrets_take_precedence_over_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(streamlit, "secrets", {KEY: "from-st"})
    monkeypatch.setenv(KEY, "from-env")
    assert get_secret(KEY) == "from-st"


def test_env_used_when_st_secrets_lacks_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(streamlit, "secrets", {"OTHER": "x"})
    monkeypatch.setenv(KEY, "from-env")
    assert get_secret(KEY) == "from-env"


def test_env_used_when_secrets_file_absent(no_st_secrets: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(KEY, "from-env")
    assert get_secret(KEY) == "from-env"


def test_none_when_neither_source_defines_secret(no_st_secrets: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(KEY, raising=False)
    assert get_secret(KEY) is None


def test_blank_values_count_as_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(streamlit, "secrets", {KEY: "  "})
    monkeypatch.setenv(KEY, "")
    assert get_secret(KEY) is None


def test_secret_values_are_never_logged(
    no_st_secrets: None, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv(KEY, "very-private-value")
    with caplog.at_level(logging.DEBUG):
        assert get_secret(KEY) == "very-private-value"
    assert "very-private-value" not in caplog.text


def test_settings_defaults_match_design() -> None:
    s = Settings()
    assert s.app_env == "development"
    assert s.pinecone_index == "hargaikan-species"
    assert s.embedding_model == "sentence-transformers/all-MiniLM-L6-v2"
    assert s.embedding_dim == 384
    assert (s.search_high, s.search_margin, s.search_low, s.search_max_candidates) == (0.80, 0.05, 0.45, 5)
    assert (s.min_price_rm_per_kg, s.max_price_rm_per_kg, s.outlier_iqr_k) == (1.0, 500.0, 3.0)
    assert (s.confidence_sederhana_min, s.confidence_tinggi_min) == (3, 10)
    assert (s.min_records_for_recommendation, s.max_recommendations) == (3, 5)
    assert s.max_budget_rm_per_kg == 1000.0
    assert (s.min_history_weeks, s.min_test_weeks, s.forecast_max_data_age_days) == (12, 2, 21)
    assert s.kl_bbox == (2.95, 101.55, 3.35, 101.80)
    assert s.service_timeout_s == 10.0


def test_settings_is_frozen() -> None:
    with pytest.raises(FrozenInstanceError):
        Settings().app_env = "production"  # type: ignore[misc]


def test_settings_repr_masks_secrets() -> None:
    s = Settings(supabase_anon_key="anon-value", pinecone_api_key="pc-value")
    assert "anon-value" not in repr(s) and "pc-value" not in repr(s)


def test_load_settings_reads_named_secrets() -> None:
    values = {
        config.APP_ENV: "test",
        config.SUPABASE_URL: "https://example.supabase.co",
        config.SUPABASE_ANON_KEY: "anon",
        config.PINECONE_INDEX_NAME: "custom-index",
        config.NOMINATIM_CONTACT: "dev@example.com",
    }
    s = load_settings(values.get)
    assert s.app_env == "test"
    assert s.supabase_url == "https://example.supabase.co"
    assert s.supabase_anon_key == "anon"
    assert s.supabase_service_role_key is None
    assert s.pinecone_api_key is None
    assert s.pinecone_index == "custom-index"
    assert s.nominatim_contact == "dev@example.com"


def test_load_settings_defaults_when_nothing_configured() -> None:
    s = load_settings(lambda name: None)
    assert s == Settings()


def test_load_settings_rejects_unknown_env_without_echoing_value() -> None:
    with pytest.raises(ValueError) as exc:
        load_settings({config.APP_ENV: "staging-xyz"}.get)
    assert "APP_ENV" in str(exc.value) and "staging-xyz" not in str(exc.value)
