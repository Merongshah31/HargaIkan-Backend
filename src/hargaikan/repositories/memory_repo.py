"""In-memory repositories over a :class:`~hargaikan.data.demo.DemoDataset` (``DATA_BACKEND=memory``).

Everything here is demo data: the price records and bands are SYNTHETIC and must be shown with
the "Data demo" label. Every row in the dataset counts as live.

:class:`MemoryUserRepository` keeps user data in a process-wide :class:`MemoryUserStore`. It is
NOT persistent (lost on restart, not shared between worker processes) and exists only so the
demo works without Supabase. Like the database, it rejects allergy data or a preferred location
without the matching consent.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

from hargaikan.data.demo import DemoDataset
from hargaikan.data.weekly import PREMISE_LEVELS, WeeklyPoint, catalog_overview, subtree_ids, weekly_medians
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


class MemoryCatalogRepository:
    def __init__(self, dataset: DemoDataset) -> None:
        self._d = dataset

    def list_species(self, active_only: bool = True) -> list[Species]:
        return [s for s in self._d.species if s.active or not active_only]

    def list_aliases(self) -> list[SpeciesAlias]:
        return list(self._d.aliases)

    def list_locations(self) -> list[Location]:
        return list(self._d.locations)

    def list_profiles(self) -> list[FishProfile]:
        return list(self._d.profiles)

    def list_similarity(self, from_species_id: str) -> list[SimilarityEdge]:
        return [e for e in self._d.similarity if e.from_species_id == from_species_id]


class MemoryPriceRepository:
    def __init__(self, dataset: DemoDataset) -> None:
        self._d = dataset
        self._locations = {loc.id: loc for loc in dataset.locations}

    def bands_for_species(self, species_id: str) -> list[PriceBand]:
        return [b for b in self._d.bands if b.species_id == species_id]

    def weekly_series(self, species_id: str, location_id: str) -> list[WeeklyPoint]:
        """Weekly weighted medians of ok records at ``location_id`` and all its descendants."""
        ids = subtree_ids(location_id, self._locations)
        return weekly_medians(
            (r.date_end, r.price_rm_per_kg, r.record_count)
            for r in self._d.price_records
            if r.species_id == species_id and r.quality_status == "ok" and r.location_id in ids
        )

    def catalog_overview(self) -> dict:
        return catalog_overview(
            sum(1 for s in self._d.species if s.active),
            sum(1 for loc in self._d.locations if loc.level in PREMISE_LEVELS),
            [b.period_end for b in self._d.bands],
            [b.band_source for b in self._d.bands],
        )


class MemoryPredictionRepository:
    """Published predictions of the dataset (empty for the demo: nothing passes the gate)."""

    def __init__(self, dataset: DemoDataset) -> None:
        self._d = dataset

    def live_prediction(self, species_id: str, location_id: str) -> PricePrediction | None:
        live = [
            p
            for p in self._d.predictions
            if p.species_id == species_id and p.location_id == location_id and p.publish_status == "live"
        ]
        return max(live, key=lambda p: p.target_start, default=None)

    def metrics_for(self, model_version: str) -> list[dict]:
        return [dict(m) for m in self._d.metrics if m.get("model_version") == model_version]


# --- Users (demo only, not persistent) ------------------------------------------------------------


class ConsentRequiredError(ValueError):
    """Mirrors the ``enforce_consent`` trigger: ``code`` is consent_allergies_required or
    consent_location_required."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass
class MemoryUserStore:
    """Process-wide user data for the demo backend, keyed by user_id. Not persistent."""

    profiles: dict[str, UserProfile] = field(default_factory=dict)
    preferences: dict[str, UserPreferences] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def clear(self) -> None:
        with self.lock:
            self.profiles.clear()
            self.preferences.clear()


DEMO_USER_STORE = MemoryUserStore()


class MemoryUserRepository:
    """UserRepository bound to one ``user_id`` (the memory analogue of a user's access token)."""

    def __init__(self, store: MemoryUserStore, user_id: str) -> None:
        if not user_id:
            raise ValueError("user_id is required")
        self._store = store
        self.user_id = user_id

    def _check_owner(self, user_id: str) -> None:
        if user_id != self.user_id:
            raise PermissionError("row belongs to another user")

    def get_profile(self) -> UserProfile | None:
        with self._store.lock:
            return self._store.profiles.get(self.user_id)

    def upsert_profile(self, p: UserProfile) -> None:
        self._check_owner(p.user_id)
        with self._store.lock:
            self._store.profiles[self.user_id] = p

    def get_preferences(self) -> UserPreferences | None:
        with self._store.lock:
            return self._store.preferences.get(self.user_id)

    def upsert_preferences(self, p: UserPreferences) -> None:
        self._check_owner(p.user_id)
        with self._store.lock:
            profile = self._store.profiles.get(self.user_id)
            consent_allergies = bool(profile and profile.consent_allergies)
            consent_location = bool(profile and profile.consent_location)
            if (p.allergy_species_ids or p.allergy_terms or p.allergy_groups) and not consent_allergies:
                raise ConsentRequiredError("consent_allergies_required")
            if p.preferred_location_id is not None and not consent_location:
                raise ConsentRequiredError("consent_location_required")
            self._store.preferences[self.user_id] = p

    def delete_all(self) -> None:
        with self._store.lock:
            self._store.preferences.pop(self.user_id, None)
            self._store.profiles.pop(self.user_id, None)


__all__ = [
    "DEMO_USER_STORE",
    "ConsentRequiredError",
    "MemoryCatalogRepository",
    "MemoryPredictionRepository",
    "MemoryPriceRepository",
    "MemoryUserRepository",
    "MemoryUserStore",
]
