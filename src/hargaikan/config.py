"""Secret and settings resolution.

Secrets are resolved from ``st.secrets`` first and from environment variables second.
Secret values are never logged or included in exception messages; only key names are.

``streamlit`` is imported lazily inside :func:`get_secret` so that offline scripts,
notebooks and pure modules can use this module without a Streamlit runtime.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, get_args

logger = logging.getLogger(__name__)

AppEnv = Literal["development", "test", "production"]
DataBackend = Literal["memory", "supabase"]

# Names of the secrets/settings read by load_settings (see .env.example).
APP_ENV = "APP_ENV"
SUPABASE_URL = "SUPABASE_URL"
SUPABASE_ANON_KEY = "SUPABASE_ANON_KEY"
SUPABASE_SERVICE_ROLE_KEY = "SUPABASE_SERVICE_ROLE_KEY"
PINECONE_API_KEY = "PINECONE_API_KEY"
PINECONE_INDEX_NAME = "PINECONE_INDEX_NAME"
NOMINATIM_CONTACT = "NOMINATIM_CONTACT"
DATA_BACKEND = "DATA_BACKEND"
CORS_ORIGINS = "CORS_ORIGINS"
SUPABASE_JWT_SECRET = "SUPABASE_JWT_SECRET"
TRANSIT_FARE_TABLE_PATH = "TRANSIT_FARE_TABLE_PATH"
DEMO_SESSION_SECRET = "DEMO_SESSION_SECRET"

DEFAULT_CORS_ORIGINS: tuple[str, ...] = ("http://localhost:5173", "http://localhost:3000")


def _clean(value: object) -> str | None:
    """Return the value as a string, treating None and blank strings as 'not defined'."""
    if value is None:
        return None
    text = str(value)
    return text if text.strip() else None


def _streamlit_secret(name: str) -> str | None:
    """Look up ``name`` in st.secrets. Returns None if Streamlit or the secrets file is absent."""
    try:
        import streamlit as st  # lazy: keeps non-app code free of a hard streamlit dependency
    except ImportError:
        return None
    try:
        secrets = st.secrets
        if name not in secrets:
            return None
        return _clean(secrets[name])
    except Exception as exc:  # no secrets.toml, parse error, etc. Never include values.
        logger.debug("st.secrets unavailable for key %s (%s)", name, type(exc).__name__)
        return None


def get_secret(name: str) -> str | None:
    """st.secrets[name] if present, else os.environ[name], else None. Never logs values.

    Blank values (empty or whitespace-only) count as not defined, so a placeholder line such as
    ``SUPABASE_URL=`` in a .env file disables the dependent section instead of passing an empty URL.
    """
    value = _streamlit_secret(name)
    if value is not None:
        return value
    return _clean(os.environ.get(name))


def get_env(name: str) -> str | None:
    """os.environ[name] (blank counts as not defined), never st.secrets.

    The FastAPI backend uses this so importing the API never imports streamlit.
    """
    return _clean(os.environ.get(name))


@dataclass(frozen=True)
class Settings:
    app_env: AppEnv = "development"
    supabase_url: str | None = None
    supabase_anon_key: str | None = None
    supabase_service_role_key: str | None = None  # scripts/notebooks only, never the app
    pinecone_api_key: str | None = None
    pinecone_index: str = "hargaikan-species"
    nominatim_contact: str | None = None
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    embedding_dim: int = 384
    search_high: float = 0.80
    search_margin: float = 0.05
    search_low: float = 0.45
    search_max_candidates: int = 5
    min_price_rm_per_kg: float = 1.0
    max_price_rm_per_kg: float = 500.0
    outlier_iqr_k: float = 3.0
    confidence_sederhana_min: int = 3
    confidence_tinggi_min: int = 10
    min_records_for_recommendation: int = 3
    max_recommendations: int = 5
    max_budget_rm_per_kg: float = 1000.0
    min_history_weeks: int = 12
    min_test_weeks: int = 2
    forecast_max_data_age_days: int = 21
    # lat_min, lng_min, lat_max, lng_max
    kl_bbox: tuple[float, float, float, float] = (2.95, 101.55, 3.35, 101.80)
    service_timeout_s: float = 10.0
    # FastAPI backend. None = auto: "supabase" when SUPABASE_URL and SUPABASE_ANON_KEY are set,
    # else "memory" (in-process demo data labelled "Data demo"). See resolved_data_backend.
    data_backend: DataBackend | None = None
    cors_origins: tuple[str, ...] = DEFAULT_CORS_ORIGINS
    supabase_jwt_secret: str | None = None  # verifies user access tokens in the API; secret
    transit_fare_table_path: str = "data/curated/transit_fares.yaml"
    # HMAC secret for demo session tokens (memory backend only). None = random per process.
    demo_session_secret: str | None = None
    # API in-process caches (supabase backend; the memory backend caches forever).
    catalog_cache_ttl_s: float = 600.0
    prediction_cache_ttl_s: float = 600.0
    auth_cache_ttl_s: float = 60.0

    def __repr__(self) -> str:  # never expose secret values in logs or tracebacks
        def mask(v: str | None) -> str:
            return "None" if v is None else "'***'"

        return (
            f"Settings(app_env={self.app_env!r}, supabase_url={mask(self.supabase_url)}, "
            f"supabase_anon_key={mask(self.supabase_anon_key)}, "
            f"supabase_service_role_key={mask(self.supabase_service_role_key)}, "
            f"supabase_jwt_secret={mask(self.supabase_jwt_secret)}, "
            f"demo_session_secret={mask(self.demo_session_secret)}, "
            f"pinecone_api_key={mask(self.pinecone_api_key)}, pinecone_index={self.pinecone_index!r}, "
            f"nominatim_contact={mask(self.nominatim_contact)}, data_backend={self.data_backend!r}, "
            f"cors_origins={self.cors_origins!r}, ...)"
        )


def resolved_data_backend(s: Settings) -> DataBackend:
    """The data backend to use: ``s.data_backend`` if set, else "supabase" when both
    SUPABASE_URL and SUPABASE_ANON_KEY are configured, else "memory" (demo data)."""
    if s.data_backend is not None:
        return s.data_backend
    return "supabase" if s.supabase_url and s.supabase_anon_key else "memory"


def parse_cors_origins(raw: str | None) -> tuple[str, ...]:
    """Comma-separated origins → tuple (blank items dropped, order kept, duplicates removed).

    None or a value with no non-blank items gives :data:`DEFAULT_CORS_ORIGINS`. A trailing "/"
    is removed because browsers send the Origin header without it.
    """
    if raw is None:
        return DEFAULT_CORS_ORIGINS
    out: list[str] = []
    for item in raw.split(","):
        origin = item.strip().rstrip("/")
        if origin and origin not in out:
            out.append(origin)
    return tuple(out) or DEFAULT_CORS_ORIGINS


def load_settings(get: Callable[[str], str | None] = get_secret) -> Settings:
    """Build Settings from secrets/environment. Thresholds keep their design defaults.

    Raises ValueError (naming the setting, not its value) if APP_ENV or DATA_BACKEND is not a
    known value.
    """
    app_env = get(APP_ENV) or "development"
    if app_env not in get_args(AppEnv):
        raise ValueError(f"{APP_ENV} must be one of {', '.join(get_args(AppEnv))}")
    data_backend = get(DATA_BACKEND)
    if data_backend is not None:
        data_backend = data_backend.strip().lower()
        if data_backend not in get_args(DataBackend):
            raise ValueError(f"{DATA_BACKEND} must be one of {', '.join(get_args(DataBackend))}")
    return Settings(
        app_env=app_env,  # type: ignore[arg-type]
        supabase_url=get(SUPABASE_URL),
        supabase_anon_key=get(SUPABASE_ANON_KEY),
        supabase_service_role_key=get(SUPABASE_SERVICE_ROLE_KEY),
        pinecone_api_key=get(PINECONE_API_KEY),
        pinecone_index=get(PINECONE_INDEX_NAME) or Settings.pinecone_index,
        nominatim_contact=get(NOMINATIM_CONTACT),
        data_backend=data_backend,  # type: ignore[arg-type]
        cors_origins=parse_cors_origins(get(CORS_ORIGINS)),
        supabase_jwt_secret=get(SUPABASE_JWT_SECRET),
        transit_fare_table_path=get(TRANSIT_FARE_TABLE_PATH) or Settings.transit_fare_table_path,
        demo_session_secret=get(DEMO_SESSION_SECRET),
    )
