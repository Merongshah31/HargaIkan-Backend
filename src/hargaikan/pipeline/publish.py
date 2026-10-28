"""Safe, idempotent publishing: validate → upsert reference → stage → validate staged → promote.

The decision logic here is pure; every database step goes through the
:class:`~hargaikan.repositories.protocols.PublishRepository` Protocol (Requirement 7, 8.3).

* ``batch_id`` is derived from the Batch content (order-independent canonical JSON → sha256 → uuid5).
* A batch that is already live is reported as such and nothing is written.
* Fact rows become visible only through ``promote`` (one database transaction). Any failure after
  staging discards the staged rows, so the existing live rows stay unchanged.
* Reference rows (species, aliases, locations, fish profiles, similarity) are upserted by
  natural key and never deleted by a publish.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import math
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from functools import cache
from typing import TYPE_CHECKING, Any, Literal, get_args, get_type_hints

from hargaikan.domain.ids import NAMESPACE
from hargaikan.domain.ids import species_id as _species_id_for_code
from hargaikan.domain.models import (
    FishProfile,
    Location,
    PricePrediction,
    PriceBand,
    PriceRecord,
    SimilarityEdge,
    Species,
    SpeciesAlias,
)
from hargaikan.pipeline.synthetic import SYNTHETIC_SOURCE

if TYPE_CHECKING:
    from hargaikan.repositories.protocols import PublishRepository

BatchKind = Literal["prices", "predictions", "curated"]
LOCATION_LEVELS = frozenset({"pasar", "premis", "kawasan", "negeri", "nasional"})
CONFIDENCE_LEVELS = frozenset({"rendah", "sederhana", "tinggi"})
METRIC_REQUIRED_KEYS = ("model_version", "scope", "decision")
METRIC_SCOPES = frozenset({"overall", "series"})


@dataclass(frozen=True)
class Batch:
    batch_id: str  # uuid5(sha256 of canonical JSON of content); see make_batch
    kind: BatchKind
    species: list[Species] = field(default_factory=list)
    aliases: list[SpeciesAlias] = field(default_factory=list)
    locations: list[Location] = field(default_factory=list)
    records: list[PriceRecord] = field(default_factory=list)
    bands: list[PriceBand] = field(default_factory=list)
    predictions: list[PricePrediction] = field(default_factory=list)
    metrics: list[dict] = field(default_factory=list)
    profiles: list[FishProfile] = field(default_factory=list)
    similarity: list[SimilarityEdge] = field(default_factory=list)

    def fact_counts(self) -> dict[str, int]:
        """Number of fact rows per fact table."""
        return {
            "price_records": len(self.records),
            "price_bands": len(self.bands),
            "price_predictions": len(self.predictions),
            "model_metrics": len(self.metrics),
        }


@dataclass(frozen=True)
class PublishReport:
    batch_id: str
    published: bool
    already_live: bool
    errors: tuple[str, ...]


# --- Content-derived batch id --------------------------------------------------------------------


def _canonical(value: Any) -> Any:
    """JSON-ready canonical form. Lists, sets and frozensets are unordered (sorted by their
    canonical JSON); tuples keep their order. Dataclasses carry their class name."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        out = {"__type__": type(value).__name__}
        for f in dataclasses.fields(value):
            out[f.name] = _canonical(getattr(value, f.name))
        return out
    if isinstance(value, Enum):
        return _canonical(value.value)
    if isinstance(value, datetime):
        return {"__datetime__": value.isoformat()}
    if isinstance(value, date):
        return {"__date__": value.isoformat()}
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return {"__float__": repr(value)}
        return value
    if isinstance(value, dict):
        return {str(k): _canonical(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, tuple):
        return [_canonical(v) for v in value]
    if isinstance(value, (list, set, frozenset)):
        items = [_canonical(v) for v in value]
        return {"__unordered__": sorted(items, key=_dumps)}
    if value is None or isinstance(value, (bool, int, str)):
        return value
    return {"__repr__": repr(value)}


def _dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def batch_id_for(content: object) -> str:
    """uuid5(NAMESPACE, "batch:" + sha256(canonical JSON of ``content``)).

    Equal content gives the same id regardless of the order of rows in lists.
    """
    digest = hashlib.sha256(_dumps(_canonical(content)).encode("utf-8")).hexdigest()
    return str(uuid.uuid5(NAMESPACE, f"batch:{digest}"))


def make_batch(
    kind: BatchKind,
    *,
    species: Iterable[Species] = (),
    aliases: Iterable[SpeciesAlias] = (),
    locations: Iterable[Location] = (),
    records: Iterable[PriceRecord] = (),
    bands: Iterable[PriceBand] = (),
    predictions: Iterable[PricePrediction] = (),
    metrics: Iterable[dict] = (),
    profiles: Iterable[FishProfile] = (),
    similarity: Iterable[SimilarityEdge] = (),
) -> Batch:
    """Build a Batch whose batch_id is derived from all of its other fields."""
    content = {
        "kind": kind,
        "species": list(species),
        "aliases": list(aliases),
        "locations": list(locations),
        "records": list(records),
        "bands": list(bands),
        "predictions": list(predictions),
        "metrics": [dict(m) for m in metrics],
        "profiles": list(profiles),
        "similarity": list(similarity),
    }
    return Batch(batch_id=batch_id_for(content), **content)  # type: ignore[arg-type]


# --- Validation ----------------------------------------------------------------------------------


def _is_optional(hint: Any) -> bool:
    """True for ``X | None`` / ``Optional[X]`` (typing.Union and types.UnionType alike)."""
    return hint is type(None) or type(None) in get_args(hint)


@cache
def _required_fields(cls: type) -> tuple[str, ...]:
    hints = get_type_hints(cls)
    return tuple(f.name for f in dataclasses.fields(cls) if not _is_optional(hints.get(f.name)))


_TABLES: tuple[tuple[str, str], ...] = (
    ("species", "species"),
    ("aliases", "species_aliases"),
    ("locations", "locations"),
    ("records", "price_records"),
    ("bands", "price_bands"),
    ("predictions", "price_predictions"),
    ("profiles", "fish_profiles"),
    ("similarity", "fish_similarity"),
)


def check_required_fields(batch: Batch) -> list[str]:
    """``null_field:{table}:{index}:{field}`` for every None (or NaN) in a non-optional field,
    plus missing required keys of metric dicts."""
    errs: list[str] = []
    for attr, table in _TABLES:
        for i, row in enumerate(getattr(batch, attr)):
            for name in _required_fields(type(row)):
                value = getattr(row, name, None)
                if value is None or (isinstance(value, float) and math.isnan(value)):
                    errs.append(f"null_field:{table}:{i}:{name}")
    for i, m in enumerate(batch.metrics):
        errs += [f"null_field:model_metrics:{i}:{k}" for k in METRIC_REQUIRED_KEYS if m.get(k) is None]
    return errs


def _gt0(x: Any) -> bool:
    try:
        return bool(x > 0)
    except TypeError:
        return False


def _record_errors(records: Sequence[PriceRecord]) -> list[str]:
    errs: list[str] = []
    for i, r in enumerate(records):
        if not _gt0(r.price_rm_per_kg) or not (isinstance(r.record_count, int) and r.record_count >= 1):
            errs.append(f"bad_price:{i}")
        if r.unit != "kg":
            errs.append(f"bad_unit:{i}")
        if r.date_start is not None and r.date_end is not None and r.date_start > r.date_end:
            errs.append(f"bad_dates:{i}")
    return errs


def _band_ok(b: PriceBand) -> bool:
    try:
        return (
            0 < b.p25 <= b.median <= b.p75
            and isinstance(b.record_count, int)
            and b.record_count >= 1
            and b.confidence_level in CONFIDENCE_LEVELS
            and (b.period_start is None or b.period_end is None or b.period_start <= b.period_end)
        )
    except TypeError:
        return False


def _prediction_ok(p: PricePrediction) -> bool:
    try:
        return _gt0(p.predicted_median) and (p.p10 is None or (p.p90 is not None and p.p10 <= p.p90))
    except TypeError:
        return False


def _similarity_ok(e: SimilarityEdge) -> bool:
    try:
        return 0.0 <= e.similarity_score <= 1.0 and e.from_species_id != e.to_species_id
    except TypeError:
        return False


def _location_errors(locations: Sequence[Location]) -> list[str]:
    errs: list[str] = []
    for i, loc in enumerate(locations):
        if loc.level not in LOCATION_LEVELS or (loc.level == "nasional") != (loc.parent_id is None):
            errs.append(f"bad_location:{i}")
    parent = {loc.id: loc.parent_id for loc in locations}
    for loc in locations:  # the hierarchy must be acyclic
        seen = {loc.id}
        p = parent.get(loc.id)
        while p is not None and p in parent:
            if p in seen:
                errs.append(f"location_cycle:{loc.id}")
                break
            seen.add(p)
            p = parent[p]
    return errs


def profile_species_id(profile: FishProfile, species: Iterable[Species] = ()) -> str:
    """Species id of a fish profile: the batch species with that code, else uuid5 of the code."""
    for sp in species:
        if sp.code == profile.species_code:
            return sp.id
    return _species_id_for_code(profile.species_code)


def referenced_ids(batch: Batch) -> tuple[set[str], set[str]]:
    """(species ids, location ids) referenced by the batch's rows."""
    sp: set[str] = set()
    lo: set[str] = set()
    for row in (*batch.records, *batch.bands, *batch.predictions):
        sp.add(row.species_id)
        lo.add(row.location_id)
    sp.update(a.species_id for a in batch.aliases)
    for e in batch.similarity:
        sp.update((e.from_species_id, e.to_species_id))
    sp.update(profile_species_id(p, batch.species) for p in batch.profiles)
    lo.update(loc.parent_id for loc in batch.locations if loc.parent_id is not None)
    for m in batch.metrics:
        if m.get("species_id") is not None:
            sp.add(str(m["species_id"]))
        if m.get("location_id") is not None:
            lo.add(str(m["location_id"]))
    return sp, lo


def is_synthetic(batch: Batch) -> bool:
    return any(r.source_name == SYNTHETIC_SOURCE for r in batch.records) or any(
        SYNTHETIC_SOURCE in (b.band_source or "") for b in batch.bands
    )


def validate_batch(batch: Batch, target_env: str, known_species: set[str], known_locations: set[str]) -> list[str]:
    """Every defect found in ``batch`` (empty list = valid). Requirement 7.3 and 8.3.

    Error codes: synthetic_to_production, empty_batch, null_field:…, bad_price:i, bad_unit:i,
    bad_dates:i, bad_band:i, bad_pred:i, bad_metric:i, bad_similarity:i, bad_location:i,
    location_cycle:id, unknown_species:id, unknown_location:id.
    """
    errs: list[str] = []
    if target_env == "production" and is_synthetic(batch):
        errs.append("synthetic_to_production")
    if batch.kind == "prices" and not batch.bands:
        errs.append("empty_batch")
    elif batch.kind == "predictions" and not batch.predictions:
        errs.append("empty_batch")
    elif batch.kind == "curated" and not (batch.profiles or batch.similarity):
        errs.append("empty_batch")
    elif batch.kind not in ("prices", "predictions", "curated"):
        errs.append("unknown_kind")
    errs += check_required_fields(batch)
    errs += _record_errors(batch.records)
    errs += [f"bad_band:{i}" for i, b in enumerate(batch.bands) if not _band_ok(b)]
    errs += [f"bad_pred:{i}" for i, p in enumerate(batch.predictions) if not _prediction_ok(p)]
    errs += [
        f"bad_metric:{i}" for i, m in enumerate(batch.metrics) if m.get("scope") is not None and m["scope"] not in METRIC_SCOPES
    ]
    errs += [f"bad_similarity:{i}" for i, e in enumerate(batch.similarity) if not _similarity_ok(e)]
    errs += _location_errors(batch.locations)
    sp_known = set(known_species) | {x.id for x in batch.species}
    lo_known = set(known_locations) | {x.id for x in batch.locations}
    sp_ref, lo_ref = referenced_ids(batch)
    errs += [f"unknown_species:{x}" for x in sorted(sp_ref - sp_known)]
    errs += [f"unknown_location:{x}" for x in sorted(lo_ref - lo_known)]
    return errs


# --- Publish -------------------------------------------------------------------------------------


def _failed(exc: BaseException) -> str:
    # Type name only: exception messages from the client may contain URLs or request details.
    return f"publish_failed:{type(exc).__name__}"


def _safe_discard(repo: "PublishRepository", batch_id: str) -> None:
    try:
        repo.discard(batch_id)
    except Exception:  # the original failure is what gets reported
        pass


def publish(batch: Batch, repo: "PublishRepository", target_env: str) -> PublishReport:
    """Validate, then stage and promote ``batch`` through ``repo``. Never raises for repo errors.

    Returns published=False with every validation error, or with ``publish_failed:<Type>`` if a
    repository step raised; staged rows are discarded in both cases. A batch that is already
    live returns published=True, already_live=True without writing anything.
    """
    try:
        known_species, known_locations = repo.known_ids()
    except Exception as exc:
        return PublishReport(batch.batch_id, False, False, (_failed(exc),))
    errors = validate_batch(batch, target_env, known_species, known_locations)
    if errors:
        return PublishReport(batch.batch_id, False, False, tuple(errors))
    try:
        if repo.is_live(batch.batch_id):
            return PublishReport(batch.batch_id, True, True, ())
    except Exception as exc:
        return PublishReport(batch.batch_id, False, False, (_failed(exc),))
    try:
        repo.upsert_reference(batch)
        repo.stage(batch)
        staged_errors = repo.validate_staged(batch)
        if staged_errors:
            _safe_discard(repo, batch.batch_id)
            return PublishReport(batch.batch_id, False, False, tuple(staged_errors))
        repo.promote(batch.batch_id)
        return PublishReport(batch.batch_id, True, False, ())
    except Exception as exc:
        _safe_discard(repo, batch.batch_id)
        return PublishReport(batch.batch_id, False, False, (_failed(exc),))
