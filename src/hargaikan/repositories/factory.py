"""Wires the repositories for the FastAPI backend from :class:`~hargaikan.config.Settings`.

* ``memory``: in-process demo data (:func:`demo_dataset`, built once per process). Everything
  is SYNTHETIC and labelled "Data demo"; user data lives in a non-persistent process-wide store.
* ``supabase``: live tables read with the anon key; user data through a per-user client that
  sends the user's access token so RLS applies. The service role key is never used here.

The backend comes from :func:`hargaikan.config.resolved_data_backend` (DATA_BACKEND, or auto:
supabase when SUPABASE_URL and SUPABASE_ANON_KEY are set, else memory).
"""

from __future__ import annotations

import functools
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hargaikan.config import DataBackend, Settings, load_settings, resolved_data_backend
from hargaikan.data.demo import DemoDataset, build_demo_dataset, repo_path
from hargaikan.data.fares import FareEntry, load_fare_table
from hargaikan.repositories.protocols import (
    CatalogRepository,
    PredictionRepository,
    PriceRepository,
    UserRepository,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Repositories:
    catalog: CatalogRepository
    prices: PriceRepository
    predictions: PredictionRepository
    backend: DataBackend

    @property
    def demo(self) -> bool:
        """True for the memory backend: every number must carry the "Data demo" label."""
        return self.backend == "memory"


@functools.lru_cache(maxsize=1)
def demo_dataset() -> DemoDataset:
    """The memory backend's dataset, built once per process (cleared by ``demo_dataset.cache_clear()``)."""
    return build_demo_dataset()


def build_repositories(settings: Settings | None = None, *, client: Any = None) -> Repositories:
    """Catalog, price and prediction repositories for the resolved backend.

    ``client`` (supabase only) overrides the anon-key client, e.g. in tests. Raises
    MissingSecretError naming SUPABASE_URL / SUPABASE_ANON_KEY when DATA_BACKEND=supabase is set
    explicitly without them.
    """
    s = settings or load_settings()
    backend = resolved_data_backend(s)
    if backend == "memory":
        from hargaikan.repositories.memory_repo import (
            MemoryCatalogRepository,
            MemoryPredictionRepository,
            MemoryPriceRepository,
        )

        d = demo_dataset()
        return Repositories(MemoryCatalogRepository(d), MemoryPriceRepository(d), MemoryPredictionRepository(d), "memory")

    from hargaikan.repositories.catalog_repo import SupabaseCatalogRepository
    from hargaikan.repositories.prediction_repo import SupabasePredictionRepository
    from hargaikan.repositories.price_repo import SupabasePriceRepository
    from hargaikan.repositories.supabase_client import anon_client

    c = client if client is not None else anon_client(s)
    return Repositories(
        SupabaseCatalogRepository(c), SupabasePriceRepository(c), SupabasePredictionRepository(c), "supabase"
    )


def user_repository_for(
    settings: Settings, user_id: str, access_token: str | None, *, client: Any = None
) -> UserRepository:
    """A UserRepository bound to one user.

    memory: the process-wide demo store keyed by ``user_id`` (the token is not used).
    supabase: a fresh anon-key client (or ``client``) carrying ``access_token``; never shared
    between users and never the service role key. Raises ValueError without a token.
    """
    if resolved_data_backend(settings) == "memory":
        from hargaikan.repositories.memory_repo import DEMO_USER_STORE, MemoryUserRepository

        return MemoryUserRepository(DEMO_USER_STORE, user_id)

    from hargaikan.repositories.user_repo import SupabaseUserRepository

    if not access_token:
        raise ValueError("access_token is required for the supabase backend")
    if client is not None:
        return SupabaseUserRepository(client, user_id, access_token)
    return SupabaseUserRepository.from_settings(settings, user_id, access_token)


def fare_table(settings: Settings) -> list[FareEntry]:
    """Entries of the maintained transit fare table (``transit_fare_table_path``).

    A missing file gives [] (no fares). An invalid file raises FareTableError, so bad fare data
    is never used silently.
    """
    path: Path = repo_path(settings.transit_fare_table_path)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.warning("transit fare table not found: %s", path)
        return []
    return load_fare_table(text)
