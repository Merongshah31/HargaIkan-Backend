"""Repository Protocols. Services and the publisher depend on these, never on supabase directly.

Every Protocol has an in-memory fake (and a failing variant) in ``tests/fakes.py``.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from hargaikan.domain.models import (
    FishProfile,
    Location,
    PriceBand,
    PricePrediction,
    SimilarityEdge,
    Species,
    SpeciesAlias,
    UserPreferences,
    UserProfile,
)

if TYPE_CHECKING:
    from hargaikan.pipeline.publish import Batch


@runtime_checkable
class CatalogRepository(Protocol):
    def list_species(self, active_only: bool = True) -> list[Species]: ...
    def list_aliases(self) -> list[SpeciesAlias]: ...
    def list_locations(self) -> list[Location]: ...
    def list_profiles(self) -> list[FishProfile]: ...
    def list_similarity(self, from_species_id: str) -> list[SimilarityEdge]: ...


@runtime_checkable
class PriceRepository(Protocol):  # reads publish_status = 'live' only
    def bands_for_species(self, species_id: str) -> list[PriceBand]: ...
    def weekly_series(self, species_id: str, location_id: str) -> list[tuple[date, float, int]]: ...
    def catalog_overview(self) -> dict: ...  # species count, premise count, latest period


@runtime_checkable
class PredictionRepository(Protocol):
    def live_prediction(self, species_id: str, location_id: str) -> PricePrediction | None: ...
    def metrics_for(self, model_version: str) -> list[dict]: ...


@runtime_checkable
class UserRepository(Protocol):  # bound to the caller's access token (RLS)
    def get_profile(self) -> UserProfile | None: ...
    def upsert_profile(self, p: UserProfile) -> None: ...
    def get_preferences(self) -> UserPreferences | None: ...
    def upsert_preferences(self, p: UserPreferences) -> None: ...
    def delete_all(self) -> None: ...  # user_profiles, user_preferences, purchase_history, travel_estimates


@runtime_checkable
class PublishRepository(Protocol):  # service role; offline scripts/notebooks only
    def known_ids(self) -> tuple[set[str], set[str]]: ...  # species ids, location ids
    def is_live(self, batch_id: str) -> bool: ...
    def upsert_reference(self, batch: Batch) -> None: ...  # species, aliases, locations, curated
    def stage(self, batch: Batch) -> None: ...  # fact rows with publish_status='staged'
    def validate_staged(self, batch: Batch) -> list[str]: ...  # row counts + DB constraints
    def promote(self, batch_id: str) -> None: ...  # rpc promote_batch, one transaction
    def discard(self, batch_id: str) -> None: ...  # rpc discard_batch
