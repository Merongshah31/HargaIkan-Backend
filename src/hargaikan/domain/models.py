"""Domain models shared by every layer.

Pure data only: no supabase, pinecone, requests or streamlit imports and no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Literal

LocationLevel = Literal["pasar", "premis", "kawasan", "negeri", "nasional"]
QualityStatus = Literal["ok", "invalid_price", "invalid_count", "invalid_date", "inconsistent_range", "outlier"]
ConfidenceLevel = Literal["rendah", "sederhana", "tinggi"]
PublishStatus = Literal["staged", "live", "retired"]
AliasLanguage = Literal["ms", "en", "local", "raw"]
PredictionMethod = Literal["random_forest", "baseline_median4"]


class SourceLabel(str, Enum):
    """The four visible provenance labels. Every displayed number carries exactly one."""

    HARGA_SEJARAH = "Harga sejarah"
    INPUT_PENGGUNA = "Input pengguna"
    ANGGARAN_MODEL = "Anggaran model"
    ANGGARAN_PERJALANAN = "Anggaran perjalanan"


@dataclass(frozen=True)
class Species:
    id: str
    code: str
    canonical_name_ms: str
    canonical_name_en: str | None
    allergen_groups: tuple[str, ...]
    active: bool


@dataclass(frozen=True)
class SpeciesAlias:
    species_id: str
    alias: str
    language: AliasLanguage


@dataclass(frozen=True)
class Location:
    id: str
    name: str
    level: LocationLevel
    parent_id: str | None
    lat: float | None
    lng: float | None


@dataclass(frozen=True)
class PriceRecord:
    species_id: str
    location_id: str
    raw_item_name: str
    grade: str | None
    unit: Literal["kg"]
    price_rm_per_kg: float
    price_low: float | None
    price_high: float | None
    record_count: int
    date_start: date
    date_end: date
    source_name: str
    source_snapshot: int
    quality_status: QualityStatus


@dataclass(frozen=True)
class PriceBand:
    species_id: str
    location_id: str
    grade: str | None  # None = all grades
    p25: float
    median: float
    p75: float
    record_count: int
    confidence_level: ConfidenceLevel
    period_start: date
    period_end: date
    band_source: str  # "IKANSIGHT" or "SYNTHETIC"


@dataclass(frozen=True)
class FishProfile:
    species_code: str
    texture: str
    taste: str
    bone_level: str
    cooking_methods: tuple[str, ...]
    allergen_groups: tuple[str, ...]
    notes: str | None


@dataclass(frozen=True)
class SimilarityEdge:
    from_species_id: str
    to_species_id: str
    similarity_score: float  # [0, 1]
    reason: str
    basis: tuple[str, ...]
    verified: bool


@dataclass(frozen=True)
class PricePrediction:
    species_id: str
    location_id: str
    target_start: date
    target_end: date
    predicted_median: float
    p10: float | None
    p90: float | None
    method: PredictionMethod
    model_version: str
    mae: float
    last_data_date: date
    valid_until: date
    publish_status: PublishStatus


@dataclass(frozen=True)
class UserPreferences:
    user_id: str
    preferred_species_ids: tuple[str, ...] = ()
    allergy_species_ids: tuple[str, ...] = ()
    allergy_terms: tuple[str, ...] = ()
    allergy_groups: tuple[str, ...] = ()
    budget_rm_per_kg: float | None = None
    preferred_location_id: str | None = None


@dataclass(frozen=True)
class UserProfile:
    user_id: str
    consent_allergies: bool
    consent_location: bool
    consent_allergies_at: datetime | None
    consent_location_at: datetime | None
