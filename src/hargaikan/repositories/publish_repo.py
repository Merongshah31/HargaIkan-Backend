"""Supabase implementation of :class:`~hargaikan.repositories.protocols.PublishRepository`.

Service role only: used by ``scripts/*.py`` and notebooks, never by the app. Reference rows are
upserted by natural key; fact rows are inserted with ``publish_status='staged'`` and become live
only through the ``promote_batch`` RPC (one transaction, migration 001). ``discard_batch``
deletes a batch's staged rows. Dates are sent as ISO strings and ids as strings.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date
from typing import Any

from hargaikan.core.text import normalise_term
from hargaikan.domain.models import (
    FishProfile,
    Location,
    PriceBand,
    PricePrediction,
    PriceRecord,
    SimilarityEdge,
    Species,
    SpeciesAlias,
)
from hargaikan.pipeline.cleaning import normalise_premise
from hargaikan.pipeline.publish import Batch, profile_species_id

FACT_TABLES = ("price_records", "price_bands", "price_predictions", "model_metrics")
DEFAULT_CHUNK_SIZE = 500
PAGE_SIZE = 1000  # PostgREST default max rows per request


# --- Row serialisation (pure) --------------------------------------------------------------------


def _iso(d: date | None) -> str | None:
    return None if d is None else d.isoformat()


def _str(x: Any) -> str | None:
    return None if x is None else str(x)


def species_row(sp: Species) -> dict[str, Any]:
    return {
        "id": str(sp.id),
        "code": sp.code,
        "canonical_name_ms": sp.canonical_name_ms,
        "canonical_name_en": sp.canonical_name_en,
        "allergen_groups": list(sp.allergen_groups),
        "active": bool(sp.active),
    }


def alias_row(a: SpeciesAlias) -> dict[str, Any]:
    return {"species_id": str(a.species_id), "alias": a.alias, "alias_norm": normalise_term(a.alias), "language": a.language}


def location_row(loc: Location) -> dict[str, Any]:
    chain = getattr(loc, "chain", None)
    branch = getattr(loc, "branch", None)
    if loc.level == "premis" and chain is None:  # plain Location: derive from the premise key
        parsed = normalise_premise(loc.name)
        chain, branch = parsed.chain, parsed.branch
    return {
        "id": str(loc.id),
        "name": loc.name,
        "level": loc.level,
        "parent_id": _str(loc.parent_id),
        "lat": loc.lat,
        "lng": loc.lng,
        "chain": chain,
        "branch": branch,
    }


def profile_row(p: FishProfile, species: Iterable[Species] = ()) -> dict[str, Any]:
    return {
        "species_id": profile_species_id(p, species),
        "texture": p.texture,
        "taste": p.taste,
        "bone_level": p.bone_level,
        "cooking_methods": list(p.cooking_methods),
        "allergen_groups": list(p.allergen_groups),
        "notes": p.notes,
    }


def similarity_row(e: SimilarityEdge) -> dict[str, Any]:
    return {
        "from_species_id": str(e.from_species_id),
        "to_species_id": str(e.to_species_id),
        "similarity_score": float(e.similarity_score),
        "reason": e.reason,
        "basis": list(e.basis),
        "verified": bool(e.verified),
    }


def _fact(batch_id: str) -> dict[str, Any]:
    return {"batch_id": str(batch_id), "publish_status": "staged"}


def record_row(r: PriceRecord, batch_id: str) -> dict[str, Any]:
    return {
        "species_id": str(r.species_id),
        "location_id": str(r.location_id),
        "raw_item_name": r.raw_item_name,
        "grade": r.grade,
        "unit": r.unit,
        "price_rm_per_kg": float(r.price_rm_per_kg),
        "price_low": r.price_low,
        "price_high": r.price_high,
        "record_count": int(r.record_count),
        "date_start": _iso(r.date_start),
        "date_end": _iso(r.date_end),
        "source_name": r.source_name,
        "source_snapshot": int(r.source_snapshot),
        "quality_status": r.quality_status,
        **_fact(batch_id),
    }


def band_row(b: PriceBand, batch_id: str) -> dict[str, Any]:
    return {
        "species_id": str(b.species_id),
        "location_id": str(b.location_id),
        "grade": b.grade,
        "p25": float(b.p25),
        "median": float(b.median),
        "p75": float(b.p75),
        "record_count": int(b.record_count),
        "confidence_level": b.confidence_level,
        "period_start": _iso(b.period_start),
        "period_end": _iso(b.period_end),
        "band_source": b.band_source,
        **_fact(batch_id),
    }


def prediction_row(p: PricePrediction, batch_id: str) -> dict[str, Any]:
    return {
        "species_id": str(p.species_id),
        "location_id": str(p.location_id),
        "target_start": _iso(p.target_start),
        "target_end": _iso(p.target_end),
        "predicted_median": float(p.predicted_median),
        "p10": p.p10,
        "p90": p.p90,
        "method": p.method,
        "model_version": p.model_version,
        "mae": float(p.mae),
        "last_data_date": _iso(p.last_data_date),
        "valid_until": _iso(p.valid_until),
        **_fact(batch_id),
    }


METRIC_COLUMNS = (
    "model_version", "scope", "species_id", "location_id", "mae", "rmse", "baseline_mae",
    "baseline_rmse", "interval_coverage", "n_test", "decision",
)


def metric_row(m: dict, batch_id: str) -> dict[str, Any]:
    row = {k: m.get(k) for k in METRIC_COLUMNS}
    for k in ("species_id", "location_id"):
        row[k] = _str(row[k])
    return {**row, **_fact(batch_id)}


def fact_rows(batch: Batch) -> dict[str, list[dict[str, Any]]]:
    """Staged rows per fact table for ``batch``."""
    bid = batch.batch_id
    return {
        "price_records": [record_row(r, bid) for r in batch.records],
        "price_bands": [band_row(b, bid) for b in batch.bands],
        "price_predictions": [prediction_row(p, bid) for p in batch.predictions],
        "model_metrics": [metric_row(m, bid) for m in batch.metrics],
    }


def unique_by(rows: Sequence[dict[str, Any]], keys: Sequence[str]) -> list[dict[str, Any]]:
    """Last row wins per conflict key: Postgres rejects an upsert that touches a row twice."""
    out: dict[tuple, dict[str, Any]] = {}
    for row in rows:
        out[tuple(row[k] for k in keys)] = row
    return list(out.values())


def location_depths(locations: Sequence[Location]) -> dict[str, int]:
    """Depth of each location within the batch (parents outside the batch count as roots)."""
    parent = {loc.id: loc.parent_id for loc in locations}
    depths: dict[str, int] = {}
    for loc in locations:
        d, p, seen = 0, parent.get(loc.id), {loc.id}
        while p is not None and p in parent and p not in seen:
            seen.add(p)
            d += 1
            p = parent[p]
        depths[loc.id] = d
    return depths


def chunks(rows: Sequence[Any], size: int) -> Iterable[Sequence[Any]]:
    for i in range(0, len(rows), size):
        yield rows[i : i + size]


# --- Repository ----------------------------------------------------------------------------------


class SupabasePublishRepository:
    """PublishRepository over a service-role supabase Client (see repositories.supabase_client)."""

    def __init__(self, client: Any, chunk_size: int = DEFAULT_CHUNK_SIZE) -> None:
        self.client = client
        self.chunk_size = chunk_size

    # -- helpers
    def _select_all(self, table: str, columns: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        start = 0
        while True:
            resp = self.client.table(table).select(columns).range(start, start + PAGE_SIZE - 1).execute()
            data = list(resp.data or [])
            rows.extend(data)
            if len(data) < PAGE_SIZE:
                return rows
            start += PAGE_SIZE

    def _upsert(self, table: str, rows: list[dict[str, Any]], on_conflict: str) -> None:
        from postgrest.types import ReturnMethod

        rows = unique_by(rows, on_conflict.split(","))
        for part in chunks(rows, self.chunk_size):
            self.client.table(table).upsert(list(part), on_conflict=on_conflict, returning=ReturnMethod.minimal).execute()

    def _count(self, table: str, batch_id: str, status: str) -> int:
        from postgrest.types import CountMethod

        resp = (
            self.client.table(table)
            .select("id", count=CountMethod.exact, head=True)
            .eq("batch_id", batch_id)
            .eq("publish_status", status)
            .execute()
        )
        return int(resp.count or 0)

    # -- catalog reads used by the ingest pipeline (not part of the Protocol)
    def list_species(self) -> list[Species]:
        rows = self._select_all("species", "id, code, canonical_name_ms, canonical_name_en, allergen_groups, active")
        return [
            Species(
                id=str(r["id"]),
                code=r["code"],
                canonical_name_ms=r["canonical_name_ms"],
                canonical_name_en=r.get("canonical_name_en"),
                allergen_groups=tuple(r.get("allergen_groups") or ()),
                active=bool(r.get("active", True)),
            )
            for r in rows
        ]

    def list_aliases(self) -> list[SpeciesAlias]:
        rows = self._select_all("species_aliases", "species_id, alias, language")
        return [SpeciesAlias(species_id=str(r["species_id"]), alias=r["alias"], language=r["language"]) for r in rows]

    # -- PublishRepository
    def known_ids(self) -> tuple[set[str], set[str]]:
        species = {str(r["id"]) for r in self._select_all("species", "id")}
        locations = {str(r["id"]) for r in self._select_all("locations", "id")}
        return species, locations

    def is_live(self, batch_id: str) -> bool:
        for table in FACT_TABLES:
            resp = (
                self.client.table(table)
                .select("id")
                .eq("batch_id", batch_id)
                .eq("publish_status", "live")
                .limit(1)
                .execute()
            )
            if resp.data:
                return True
        return False

    def upsert_reference(self, batch: Batch) -> None:
        if batch.species:
            self._upsert("species", [species_row(s) for s in batch.species], "code")
        if batch.locations:  # parents before children
            depths = location_depths(batch.locations)
            for depth in sorted(set(depths.values())):
                level_rows = [location_row(loc) for loc in batch.locations if depths[loc.id] == depth]
                self._upsert("locations", level_rows, "level,name")
        if batch.aliases:
            self._upsert("species_aliases", [alias_row(a) for a in batch.aliases], "species_id,alias_norm")
        if batch.profiles:
            self._upsert("fish_profiles", [profile_row(p, batch.species) for p in batch.profiles], "species_id")
        if batch.similarity:
            self._upsert(
                "fish_similarity", [similarity_row(e) for e in batch.similarity], "from_species_id,to_species_id"
            )

    def stage(self, batch: Batch) -> None:
        from postgrest.types import ReturnMethod

        for table, rows in fact_rows(batch).items():
            for part in chunks(rows, self.chunk_size):
                self.client.table(table).insert(list(part), returning=ReturnMethod.minimal).execute()

    def validate_staged(self, batch: Batch) -> list[str]:
        """Staged row counts must equal the batch's fact counts. Column constraints (price > 0,
        p25 ≤ median ≤ p75, …) are enforced by the database at insert time, and live-key
        uniqueness by promote_batch, so a violation surfaces as an error from stage/promote."""
        errors: list[str] = []
        for table, expected in batch.fact_counts().items():
            actual = self._count(table, batch.batch_id, "staged")
            if actual != expected:
                errors.append(f"staged_count_mismatch:{table}:expected={expected}:actual={actual}")
        return errors

    def promote(self, batch_id: str) -> None:
        self.client.rpc("promote_batch", {"p_batch": str(batch_id)}).execute()

    def discard(self, batch_id: str) -> None:
        self.client.rpc("discard_batch", {"p_batch": str(batch_id)}).execute()
