"""Supabase row ↔ domain dataclass mapping and paging, shared by the Supabase repositories.

The mapping functions are pure. PostgREST returns dates and timestamps as ISO strings, numeric
columns as JSON numbers (or strings for very precise values), uuids as strings and arrays as
JSON lists; they become ``date``/``datetime``, ``float``, ``str`` and ``tuple`` here.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import date, datetime
from typing import Any

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
from hargaikan.pipeline.locations import PremiseLocation

PAGE_SIZE = 1000  # PostgREST default max rows per request
LIVE = "live"

SPECIES_COLUMNS = "id, code, canonical_name_ms, canonical_name_en, allergen_groups, active"
ALIAS_COLUMNS = "species_id, alias, language"
LOCATION_COLUMNS = "id, name, level, parent_id, lat, lng, chain, branch"
PROFILE_COLUMNS = "species_id, texture, taste, bone_level, cooking_methods, allergen_groups, notes"
SIMILARITY_COLUMNS = "from_species_id, to_species_id, similarity_score, reason, basis, verified"
BAND_COLUMNS = (
    "species_id, location_id, grade, p25, median, p75, record_count, confidence_level, "
    "period_start, period_end, band_source"
)
PREDICTION_COLUMNS = (
    "species_id, location_id, target_start, target_end, predicted_median, p10, p90, method, "
    "model_version, mae, last_data_date, valid_until, publish_status"
)
METRIC_COLUMNS = (
    "model_version, scope, species_id, location_id, mae, rmse, baseline_mae, baseline_rmse, "
    "interval_coverage, n_test, decision"
)
USER_PROFILE_COLUMNS = "user_id, consent_allergies, consent_allergies_at, consent_location, consent_location_at"
USER_PREFERENCES_COLUMNS = (
    "user_id, preferred_species_ids, allergy_species_ids, allergy_terms, allergy_groups, "
    "budget_rm_per_kg, preferred_location_id"
)


# --- Scalars --------------------------------------------------------------------------------------


def to_date(v: Any) -> date | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return date.fromisoformat(str(v)[:10])


def to_datetime(v: Any) -> datetime | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v
    return datetime.fromisoformat(str(v).replace("Z", "+00:00"))


def to_float(v: Any) -> float | None:
    return None if v is None else float(v)


def to_str(v: Any) -> str | None:
    return None if v is None else str(v)


def to_tuple(v: Any) -> tuple:
    return tuple(str(x) for x in (v or ()))


# --- Catalog --------------------------------------------------------------------------------------


def species_from_row(r: Mapping[str, Any]) -> Species:
    return Species(
        id=str(r["id"]),
        code=r["code"],
        canonical_name_ms=r["canonical_name_ms"],
        canonical_name_en=r.get("canonical_name_en"),
        allergen_groups=to_tuple(r.get("allergen_groups")),
        active=bool(r.get("active", True)),
    )


def alias_from_row(r: Mapping[str, Any]) -> SpeciesAlias:
    return SpeciesAlias(species_id=str(r["species_id"]), alias=r["alias"], language=r["language"])


def location_from_row(r: Mapping[str, Any]) -> Location:
    common = dict(
        id=str(r["id"]),
        name=r["name"],
        level=r["level"],
        parent_id=to_str(r.get("parent_id")),
        lat=to_float(r.get("lat")),
        lng=to_float(r.get("lng")),
    )
    if r["level"] == "premis":
        return PremiseLocation(**common, chain=r.get("chain"), branch=r.get("branch"))
    return Location(**common)


def profile_from_row(r: Mapping[str, Any], code_by_id: Mapping[str, str]) -> FishProfile | None:
    """None when the species id has no known code. Null text columns become ""."""
    code = code_by_id.get(str(r["species_id"]))
    if code is None:
        return None
    return FishProfile(
        species_code=code,
        texture=r.get("texture") or "",
        taste=r.get("taste") or "",
        bone_level=r.get("bone_level") or "",
        cooking_methods=to_tuple(r.get("cooking_methods")),
        allergen_groups=to_tuple(r.get("allergen_groups")),
        notes=r.get("notes"),
    )


def similarity_from_row(r: Mapping[str, Any]) -> SimilarityEdge:
    return SimilarityEdge(
        from_species_id=str(r["from_species_id"]),
        to_species_id=str(r["to_species_id"]),
        similarity_score=float(r["similarity_score"]),
        reason=r["reason"],
        basis=to_tuple(r.get("basis")),
        verified=bool(r.get("verified", False)),
    )


# --- Facts ----------------------------------------------------------------------------------------


def band_from_row(r: Mapping[str, Any]) -> PriceBand:
    return PriceBand(
        species_id=str(r["species_id"]),
        location_id=str(r["location_id"]),
        grade=r.get("grade"),
        p25=float(r["p25"]),
        median=float(r["median"]),
        p75=float(r["p75"]),
        record_count=int(r["record_count"]),
        confidence_level=r["confidence_level"],
        period_start=to_date(r["period_start"]),  # type: ignore[arg-type]
        period_end=to_date(r["period_end"]),  # type: ignore[arg-type]
        band_source=r["band_source"],
    )


def prediction_from_row(r: Mapping[str, Any]) -> PricePrediction:
    return PricePrediction(
        species_id=str(r["species_id"]),
        location_id=str(r["location_id"]),
        target_start=to_date(r["target_start"]),  # type: ignore[arg-type]
        target_end=to_date(r["target_end"]),  # type: ignore[arg-type]
        predicted_median=float(r["predicted_median"]),
        p10=to_float(r.get("p10")),
        p90=to_float(r.get("p90")),
        method=r["method"],
        model_version=r["model_version"],
        mae=float(r["mae"]),
        last_data_date=to_date(r["last_data_date"]),  # type: ignore[arg-type]
        valid_until=to_date(r["valid_until"]),  # type: ignore[arg-type]
        publish_status=r.get("publish_status", LIVE),
    )


_METRIC_FLOATS = ("mae", "rmse", "baseline_mae", "baseline_rmse", "interval_coverage")


def metric_from_row(r: Mapping[str, Any]) -> dict:
    out = dict(r)
    for k in _METRIC_FLOATS:
        if k in out:
            out[k] = to_float(out[k])
    if out.get("n_test") is not None:
        out["n_test"] = int(out["n_test"])
    for k in ("species_id", "location_id"):
        if k in out:
            out[k] = to_str(out[k])
    return out


# --- Users ----------------------------------------------------------------------------------------


def _iso_dt(v: datetime | None) -> str | None:
    return None if v is None else v.isoformat()


def user_profile_from_row(r: Mapping[str, Any]) -> UserProfile:
    return UserProfile(
        user_id=str(r["user_id"]),
        consent_allergies=bool(r.get("consent_allergies", False)),
        consent_location=bool(r.get("consent_location", False)),
        consent_allergies_at=to_datetime(r.get("consent_allergies_at")),
        consent_location_at=to_datetime(r.get("consent_location_at")),
    )


def user_profile_row(p: UserProfile) -> dict[str, Any]:
    return {
        "user_id": str(p.user_id),
        "consent_allergies": bool(p.consent_allergies),
        "consent_allergies_at": _iso_dt(p.consent_allergies_at),
        "consent_location": bool(p.consent_location),
        "consent_location_at": _iso_dt(p.consent_location_at),
    }


def user_preferences_from_row(r: Mapping[str, Any]) -> UserPreferences:
    return UserPreferences(
        user_id=str(r["user_id"]),
        preferred_species_ids=to_tuple(r.get("preferred_species_ids")),
        allergy_species_ids=to_tuple(r.get("allergy_species_ids")),
        allergy_terms=to_tuple(r.get("allergy_terms")),
        allergy_groups=to_tuple(r.get("allergy_groups")),
        budget_rm_per_kg=to_float(r.get("budget_rm_per_kg")),
        preferred_location_id=to_str(r.get("preferred_location_id")),
    )


def user_preferences_row(p: UserPreferences) -> dict[str, Any]:
    return {
        "user_id": str(p.user_id),
        "preferred_species_ids": list(p.preferred_species_ids),
        "allergy_species_ids": list(p.allergy_species_ids),
        "allergy_terms": list(p.allergy_terms),
        "allergy_groups": list(p.allergy_groups),
        "budget_rm_per_kg": None if p.budget_rm_per_kg is None else float(p.budget_rm_per_kg),
        "preferred_location_id": to_str(p.preferred_location_id),
    }


# --- Paging ---------------------------------------------------------------------------------------


def fetch_all(build: Callable[[], Any], page_size: int = PAGE_SIZE) -> list[dict[str, Any]]:
    """Every row of a query, page by page. ``build()`` must return a fresh, ordered query builder
    (filters applied, no range) so pages are stable."""
    rows: list[dict[str, Any]] = []
    start = 0
    while True:
        resp = build().range(start, start + page_size - 1).execute()
        data = list(resp.data or [])
        rows.extend(data)
        if len(data) < page_size:
            return rows
        start += page_size
