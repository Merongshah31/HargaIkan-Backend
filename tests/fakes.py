"""In-memory fakes of the repository Protocols (and failing variants) for unit and property tests.

``FakePublishRepository`` mirrors the database semantics of migration 001: reference rows are
upserted by natural key, fact rows are staged per batch, ``promote`` swaps staged → live in one
step (retiring the previous live rows of every touched table) and ``discard`` deletes a batch's
staged rows. Failures can be injected per method.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

from hargaikan.core.text import normalise_term
from hargaikan.domain.models import (
    FishProfile,
    Location,
    PriceBand,
    PricePrediction,
    SimilarityEdge,
    Species,
    SpeciesAlias,
)
from hargaikan.pipeline.publish import Batch, profile_species_id

FACT_TABLES = ("price_records", "price_bands", "price_predictions", "model_metrics")
PUBLISH_METHODS = ("known_ids", "is_live", "upsert_reference", "stage", "validate_staged", "promote", "discard")


class InjectedFailure(RuntimeError):
    """Raised by a fake when a failure was injected for the called method."""


def freeze(row: Any) -> Any:
    """Hashable form of a fact row (dataclasses are frozen already; metric dicts become tuples)."""
    if isinstance(row, dict):
        return tuple(sorted((k, freeze(v)) for k, v in row.items()))
    if isinstance(row, list):
        return tuple(freeze(v) for v in row)
    return row


def batch_facts(batch: Batch) -> dict[str, Counter]:
    """Fact rows of ``batch`` per table, as multisets (the expected live state after a publish)."""
    return {
        "price_records": Counter(batch.records),
        "price_bands": Counter(batch.bands),
        "price_predictions": Counter(batch.predictions),
        "model_metrics": Counter(freeze(m) for m in batch.metrics),
    }


def _batch_rows(batch: Batch) -> list[tuple[str, Any]]:
    out: list[tuple[str, Any]] = []
    for table, counter in batch_facts(batch).items():
        for row, n in counter.items():
            out.extend([(table, row)] * n)
    return out


@dataclass
class FactRow:
    table: str
    batch_id: str
    status: str  # staged | live | retired
    row: Any


class FakePublishRepository:
    """In-memory PublishRepository with live/staged state and failure injection.

    ``fail_on``: method names that raise InjectedFailure. ``stage`` fails after writing half of
    the rows (a partial write), ``promote`` fails before changing anything (it is transactional).
    ``staged_errors``: extra errors returned by ``validate_staged``.
    """

    def __init__(
        self,
        species_ids: Iterable[str] = (),
        location_ids: Iterable[str] = (),
        fail_on: Iterable[str] = (),
        staged_errors: Sequence[str] = (),
    ) -> None:
        self.extra_species_ids = set(species_ids)
        self.extra_location_ids = set(location_ids)
        self.species: dict[str, Species] = {}  # by code (natural key)
        self.aliases: dict[tuple[str, str], SpeciesAlias] = {}  # by (species_id, alias_norm)
        self.locations: dict[tuple[str, str], Location] = {}  # by (level, name)
        self.profiles: dict[str, FishProfile] = {}  # by species_id
        self.similarity: dict[tuple[str, str], SimilarityEdge] = {}  # by (from, to)
        self.facts: list[FactRow] = []
        self.fail_on = set(fail_on)
        self.staged_errors = list(staged_errors)
        self.calls: list[str] = []

    def _enter(self, name: str) -> None:
        self.calls.append(name)
        if name in self.fail_on and name != "stage":
            raise InjectedFailure(name)

    # -- inspection helpers
    def live_state(self) -> dict[str, Counter]:
        return self._state("live")

    def staged_state(self) -> dict[str, Counter]:
        return self._state("staged")

    def _state(self, status: str) -> dict[str, Counter]:
        out = {t: Counter() for t in FACT_TABLES}
        for f in self.facts:
            if f.status == status:
                out[f.table][f.row] += 1
        return out

    def reference_state(self) -> dict[str, dict]:
        return {
            "species": dict(self.species),
            "aliases": dict(self.aliases),
            "locations": dict(self.locations),
            "profiles": dict(self.profiles),
            "similarity": dict(self.similarity),
        }

    def seed_live(self, batch: Batch) -> None:
        """Make ``batch``'s reference and fact rows live directly (initial state for tests)."""
        self._upsert_reference(batch)
        self.facts.extend(FactRow(t, batch.batch_id, "live", row) for t, row in _batch_rows(batch))

    # -- PublishRepository
    def known_ids(self) -> tuple[set[str], set[str]]:
        self._enter("known_ids")
        species = {s.id for s in self.species.values()} | self.extra_species_ids
        locations = {loc.id for loc in self.locations.values()} | self.extra_location_ids
        return species, locations

    def is_live(self, batch_id: str) -> bool:
        self._enter("is_live")
        return any(f.batch_id == batch_id and f.status == "live" for f in self.facts)

    def upsert_reference(self, batch: Batch) -> None:
        self._enter("upsert_reference")
        self._upsert_reference(batch)

    def _upsert_reference(self, batch: Batch) -> None:
        for s in batch.species:
            self.species[s.code] = s
        for loc in batch.locations:
            self.locations[(loc.level, loc.name)] = loc
        for a in batch.aliases:
            self.aliases[(a.species_id, normalise_term(a.alias))] = a
        for p in batch.profiles:
            self.profiles[profile_species_id(p, batch.species)] = p
        for e in batch.similarity:
            self.similarity[(e.from_species_id, e.to_species_id)] = e

    def stage(self, batch: Batch) -> None:
        self._enter("stage")
        rows = _batch_rows(batch)
        if "stage" in self.fail_on:  # partial write, then failure
            half = rows[: len(rows) // 2]
            self.facts.extend(FactRow(t, batch.batch_id, "staged", row) for t, row in half)
            raise InjectedFailure("stage")
        self.facts.extend(FactRow(t, batch.batch_id, "staged", row) for t, row in rows)

    def validate_staged(self, batch: Batch) -> list[str]:
        self._enter("validate_staged")
        errors: list[str] = []
        for table, expected in batch.fact_counts().items():
            actual = sum(1 for f in self.facts if f.batch_id == batch.batch_id and f.status == "staged" and f.table == table)
            if actual != expected:
                errors.append(f"staged_count_mismatch:{table}:expected={expected}:actual={actual}")
        return errors + list(self.staged_errors)

    def promote(self, batch_id: str) -> None:
        self._enter("promote")
        touched = {f.table for f in self.facts if f.batch_id == batch_id and f.status == "staged"}
        if not touched:
            raise RuntimeError(f"promote_batch: batch {batch_id} has no staged rows")
        kept: list[FactRow] = []
        for f in self.facts:
            if f.table in touched and f.status == "retired":
                continue  # delete the previous rollback copy
            if f.table in touched and f.status == "live" and f.batch_id != batch_id:
                f.status = "retired"
            elif f.table in touched and f.status == "staged" and f.batch_id == batch_id:
                f.status = "live"
            kept.append(f)
        self.facts = kept

    def discard(self, batch_id: str) -> None:
        self._enter("discard")
        self.facts = [f for f in self.facts if not (f.batch_id == batch_id and f.status == "staged")]


class FailingPublishRepository(FakePublishRepository):
    """Every PublishRepository method raises InjectedFailure."""

    def __init__(self) -> None:
        super().__init__(fail_on=PUBLISH_METHODS)


# --- Catalog and price repositories (minimal in-memory skeletons) --------------------------------


class FakeCatalogRepository:
    def __init__(
        self,
        species: Sequence[Species] = (),
        aliases: Sequence[SpeciesAlias] = (),
        locations: Sequence[Location] = (),
        profiles: Sequence[FishProfile] = (),
        similarity: Sequence[SimilarityEdge] = (),
    ) -> None:
        self.species, self.aliases, self.locations = list(species), list(aliases), list(locations)
        self.profiles, self.similarity = list(profiles), list(similarity)

    def list_species(self, active_only: bool = True) -> list[Species]:
        return [s for s in self.species if s.active or not active_only]

    def list_aliases(self) -> list[SpeciesAlias]:
        return list(self.aliases)

    def list_locations(self) -> list[Location]:
        return list(self.locations)

    def list_profiles(self) -> list[FishProfile]:
        return list(self.profiles)

    def list_similarity(self, from_species_id: str) -> list[SimilarityEdge]:
        return [e for e in self.similarity if e.from_species_id == from_species_id]


class FailingCatalogRepository(FakeCatalogRepository):
    def _fail(self, *args: Any, **kwargs: Any) -> Any:
        raise InjectedFailure("catalog")

    list_species = list_aliases = list_locations = list_profiles = list_similarity = _fail  # type: ignore[assignment]


class FakePriceRepository:
    """Live bands and pre-computed weekly series keyed by (species_id, location_id)."""

    def __init__(
        self,
        bands: Sequence[PriceBand] = (),
        series: dict[tuple[str, str], list[tuple[date, float, int]]] | None = None,
        overview: dict | None = None,
    ) -> None:
        self.bands = list(bands)
        self.series = dict(series or {})
        self.overview = dict(overview or {})

    def bands_for_species(self, species_id: str) -> list[PriceBand]:
        return [b for b in self.bands if b.species_id == species_id]

    def weekly_series(self, species_id: str, location_id: str) -> list[tuple[date, float, int]]:
        return list(self.series.get((species_id, location_id), []))

    def catalog_overview(self) -> dict:
        return dict(self.overview)


class FailingPriceRepository(FakePriceRepository):
    def _fail(self, *args: Any, **kwargs: Any) -> Any:
        raise InjectedFailure("prices")

    bands_for_species = weekly_series = catalog_overview = _fail  # type: ignore[assignment]


class FakePredictionRepository:
    def __init__(self, predictions: Sequence[PricePrediction] = (), metrics: Sequence[dict] = ()) -> None:
        self.predictions, self.metrics = list(predictions), list(metrics)

    def live_prediction(self, species_id: str, location_id: str) -> PricePrediction | None:
        for p in self.predictions:
            if p.species_id == species_id and p.location_id == location_id and p.publish_status == "live":
                return p
        return None

    def metrics_for(self, model_version: str) -> list[dict]:
        return [m for m in self.metrics if m.get("model_version") == model_version]


class FailingPredictionRepository(FakePredictionRepository):
    def _fail(self, *args: Any, **kwargs: Any) -> Any:
        raise InjectedFailure("predictions")

    live_prediction = metrics_for = _fail  # type: ignore[assignment]
