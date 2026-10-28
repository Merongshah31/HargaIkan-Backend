"""FastAPI application factory and the ASGI entry point ``hargaikan.api.main:app``.

Run locally::

    D:\\hargaikan-venv\\Scripts\\python.exe -m uvicorn hargaikan.api.main:app --reload

Importing this module builds the app from environment variables (``.env`` in the working
directory is loaded if present) but never touches the network: repositories are built on first
use (the memory demo dataset is warmed at startup, errors handled). Streamlit, torch and
sentence-transformers are never imported.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from hargaikan import __version__
from hargaikan.api import routes, routes_me
from hargaikan.api.container import AppContainer, UserRepoFactory
from hargaikan.api.errors import install_error_handlers
from hargaikan.config import Settings, get_env, load_settings
from hargaikan.data.fares import FareEntry
from hargaikan.repositories.factory import Repositories
from hargaikan.repositories.supabase_client import safe_error
from hargaikan.services.search_service import VectorSearch

logger = logging.getLogger(__name__)

API_PREFIX = "/api/v1"


def _load_dotenv() -> None:
    """Load ``.env`` from the working directory for local runs (never overrides real env vars)."""
    try:
        from dotenv import load_dotenv
    except ImportError:  # optional
        return
    path = Path.cwd() / ".env"
    if path.is_file():
        load_dotenv(path, override=False)


def create_app(
    settings: Settings | None = None,
    repositories: Repositories | None = None,
    *,
    user_repo_factory: UserRepoFactory | None = None,
    vector_search: VectorSearch | None = None,
    fare_entries: list[FareEntry] | None = None,
    http_client: httpx.Client | None = None,
    today: Callable[[], date] = date.today,
) -> FastAPI:
    """Build the API. Every argument is injectable for tests; defaults come from the environment.

    ``settings`` default: :func:`load_settings` with environment variables only (no st.secrets).
    ``repositories`` default: :func:`build_repositories` on first use.
    """
    s = settings if settings is not None else load_settings(get_env)
    container = AppContainer(
        s,
        repositories,
        user_repo_factory=user_repo_factory,
        vector_search=vector_search,
        fare_entries=fare_entries,
        http_client=http_client,
        today=today,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if container.demo:  # warm the in-process demo dataset; never fatal
            try:
                container.services().catalog.snapshot()
            except Exception as exc:
                logger.warning("demo warm-up failed: %s", safe_error(exc))
        yield

    app = FastAPI(
        title="HargaIkan API",
        version=__version__,
        description="Historical fish reference prices for Kuala Lumpur. Every number carries a source label.",
        lifespan=lifespan,
    )
    app.state.container = container
    # Explicit origins only (no wildcard) because Authorization headers are allowed.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o for o in s.cors_origins if o != "*"],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
        max_age=600,
    )
    install_error_handlers(app)
    app.include_router(routes.router, prefix=API_PREFIX)
    app.include_router(routes_me.router, prefix=API_PREFIX)
    return app


_load_dotenv()
app = create_app()
