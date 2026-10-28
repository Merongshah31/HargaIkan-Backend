"""Pydantic v2 request/response models for ``/api/v1``.

Every block that carries a price or cost number has ``source_label`` (one of the four Source_Labels
or "Jadual tambang") and ``demo`` (true when the number comes from SYNTHETIC demo data; the UI
then shows "Data demo"). Travel money values are 2-dp strings; unavailable values are ``null``
with a BM ``display`` text instead of a number.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

SourceLabelText = Literal["Harga sejarah", "Input pengguna", "Anggaran model", "Anggaran perjalanan", "Jadual tambang"]
# Money / quantity input: a JSON number or a short string such as "RM6.00" (parsed with Decimal).
Number = float | int | Annotated[str, StringConstraints(max_length=32)]
ShortText = Annotated[str, StringConstraints(max_length=200)]

_MAX_IDS = 50
_MAX_TEXT = 200


class _Out(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- errors ------------------------------------------------------------------------------------


class ErrorDetail(_Out):
    code: str
    messages: list[str]
    fields: dict[str, str] | None = None


class ErrorResponse(_Out):
    detail: ErrorDetail


# --- system ------------------------------------------------------------------------------------


class HealthOut(_Out):
    status: Literal["ok"]
    backend: Literal["memory", "supabase"]
    demo: bool
    version: str


class IntegrationOut(_Out):
    name: str
    connected: bool
    note: str | None = None


class FareTableOut(_Out):
    connected: bool
    entries: int
    note: str | None = None


class ForecastModelOut(_Out):
    available: bool | None  # None = checked per species/location (supabase)
    note: str


class CapabilitiesOut(_Out):
    backend: Literal["memory", "supabase"]
    demo: bool
    integrations: list[IntegrationOut]
    supabase: bool
    pinecone: bool
    weather: bool
    routing: bool
    transit_api: bool
    transit_fare_table: FareTableOut
    forecast_model: ForecastModelOut


class LegendItemOut(_Out):
    label: str
    icon: str
    description: str


# --- catalog -----------------------------------------------------------------------------------


class AliasOut(_Out):
    alias: str
    language: str


class FishProfileOut(_Out):
    texture: str
    taste: str
    bone_level: str
    cooking_methods: list[str]
    allergen_groups: list[str]
    notes: str | None


class SpeciesOut(_Out):
    id: str
    code: str
    name_ms: str
    name_en: str | None
    allergen_groups: list[str]
    aliases: list[AliasOut]
    profile: FishProfileOut | None


class SpeciesListOut(_Out):
    count: int
    items: list[SpeciesOut]
    demo: bool


class LocationOut(_Out):
    id: str
    name: str
    level: Literal["pasar", "premis", "kawasan", "negeri", "nasional"]
    parent_id: str | None
    lat: float | None
    lng: float | None
    has_coordinates: bool


class LocationListOut(_Out):
    count: int
    items: list[LocationOut]


class LocationRef(_Out):
    id: str
    name: str
    level: str


class SpeciesRef(_Out):
    id: str
    name_ms: str


# --- search ------------------------------------------------------------------------------------


class SearchMatchOut(_Out):
    species_id: str
    name_ms: str
    matched_alias: str
    score: float


class SearchOut(_Out):
    query: str
    status: Literal["confident", "candidates", "none", "empty_query"]
    matches: list[SearchMatchOut]
    backend: Literal["exact", "pinecone", "fuzzy"]
    degraded: bool
    notice: str | None
    confirm_prompt: str | None


# --- prices ------------------------------------------------------------------------------------


class BandOut(_Out):
    p25: float
    median: float
    p75: float
    record_count: int
    confidence_level: Literal["rendah", "sederhana", "tinggi"]
    period_start: date
    period_end: date
    grade: str | None
    band_source: str
    location: LocationRef
    source_label: Literal["Harga sejarah"] = "Harga sejarah"
    demo: bool
    demo_label: str | None


class QuoteOut(_Out):
    value: float
    source_label: Literal["Input pengguna"] = "Input pengguna"
    demo: bool = False


class VerdictOut(_Out):
    kind: Literal["below", "within", "above", "no_data"]
    text: str
    icon: str


class WeeklyPointOut(_Out):
    week_start: date
    median: float
    record_count: int


class ChartOut(_Out):
    location: LocationRef
    points: list[WeeklyPointOut]
    band_p25: float
    band_p75: float
    source_label: Literal["Harga sejarah"] = "Harga sejarah"
    demo: bool
    quote: QuoteOut | None


class ReferenceOut(_Out):
    species: SpeciesRef
    requested_location: LocationRef
    used_location: LocationRef | None
    available: bool
    is_fallback: bool
    message: str | None
    band: BandOut | None
    grade: str | None
    grades: list[str]
    quote: QuoteOut | None
    quote_error: str | None
    verdict: VerdictOut | None
    chart: ChartOut | None
    chart_notice: str | None
    demo: bool


# --- recommendations ---------------------------------------------------------------------------


class RecommendationItemOut(_Out):
    species: SpeciesRef
    reason: str
    differences: list[str]
    p25: float
    median: float
    p75: float
    savings_rm_per_kg: float
    record_count: int
    confidence_level: str
    location_name: str
    period_start: date
    period_end: date
    similarity_score: float
    over_budget: bool
    over_budget_label: str | None
    source_label: Literal["Harga sejarah"] = "Harga sejarah"
    demo: bool
    demo_label: str | None


class RecommendationsOut(_Out):
    species: SpeciesRef
    location: LocationRef
    items: list[RecommendationItemOut]
    empty_message: str | None
    empty_reason: str | None
    original_allergy_match: bool
    allergy_caution: str | None
    disclaimer: str
    personalised: bool
    notice: str | None = None


# --- forecast ----------------------------------------------------------------------------------


class PredictionOut(_Out):
    predicted_median: float
    p10: float | None
    p90: float | None
    method: str
    model_version: str
    mae: float
    target_start: date
    target_end: date
    last_data_date: date
    is_baseline: bool
    location: LocationRef
    source_label: Literal["Anggaran model"] = "Anggaran model"
    demo: bool


class ForecastOut(_Out):
    species: SpeciesRef
    location: LocationRef
    available: bool
    message: str | None
    prediction: PredictionOut | None
    p75_message: str | None
    early_buy_message: str | None
    quote: QuoteOut | None
    quote_error: str | None


# --- travel ------------------------------------------------------------------------------------


class TravelIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["kereta", "pengangkutan_awam"]
    distance_km: Number | None = None
    fuel_efficiency_km_per_l: Number | None = None
    fuel_type: Literal["RON95", "RON97"] = "RON95"
    fuel_price_rm_per_l: Number | None = None
    toll_rm: Number | None = None
    parking_rm: Number | None = None
    transit_fare_rm: Number | None = None
    transit_mode: Literal["mrt", "lrt", "monorel", "bas"] | None = None
    origin: str | None = Field(default=None, max_length=_MAX_TEXT)
    destination: str | None = Field(default=None, max_length=_MAX_TEXT)
    return_trip: bool = False
    fish_price_rm_per_kg: Number | None = None
    qty_kg: Number | None = None


class TravelValueOut(_Out):
    key: str
    name: str
    value: str | None  # 2 dp; null when not computed
    display: str  # value, or "Belum dikira"
    unit: str
    source_label: SourceLabelText
    display_label: str
    demo: bool = False
    note: str | None = None


class TravelOut(_Out):
    mode: str
    fuel_type: str
    return_trip: bool
    estimated: bool
    message: str | None
    inputs: list[TravelValueOut]
    components: list[TravelValueOut]
    travel_total: TravelValueOut
    fish_cost: TravelValueOut
    grand_total: TravelValueOut
    effective_rm_per_kg: TravelValueOut
    fare_source: SourceLabelText | None
    fare_source_detail: str | None
    notes: list[str]
    demo: bool = False


# --- overview / dashboard ----------------------------------------------------------------------


class SuggestionOut(_Out):
    species: SpeciesRef
    location: LocationRef
    p25: float
    median: float
    p75: float
    record_count: int
    confidence_level: str
    period_start: date
    period_end: date
    source_label: Literal["Harga sejarah"] = "Harga sejarah"
    demo: bool
    demo_label: str | None


class OverviewOut(_Out):
    species_count: int | None
    premise_count: int | None
    latest_period: date | None
    demo: bool
    demo_label: str | None
    notice: str | None
    legend: list[LegendItemOut]
    suggestions_title: str
    suggestions: list[SuggestionOut]
    suggestions_notice: str | None


class BudgetOut(_Out):
    budget_rm_per_kg: float
    budget_source_label: Literal["Input pengguna"] = "Input pengguna"
    median: float
    source_label: Literal["Harga sejarah"] = "Harga sejarah"
    within_budget: bool
    text: str
    demo: bool


class DashboardItemOut(_Out):
    species: SpeciesRef
    reference: ReferenceOut | None
    reference_notice: str | None
    recommendations: RecommendationsOut | None
    recommendations_notice: str | None
    forecast: ForecastOut | None
    forecast_notice: str | None
    budget: BudgetOut | None


class DashboardOut(_Out):
    state: Literal["empty", "personalised"]
    message: str | None
    location: LocationRef | None
    location_note: str | None
    items: list[DashboardItemOut]
    overview: OverviewOut | None


# --- session / me ------------------------------------------------------------------------------


class DemoSessionOut(_Out):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    user_id: str
    demo: bool = True
    notice: str


class MeOut(_Out):
    user_id: str
    backend: Literal["memory", "supabase"]
    demo: bool
    session_notice: str


class ProfileIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preferred_species_ids: list[ShortText] = Field(default_factory=list, max_length=_MAX_IDS)
    allergy_species_ids: list[ShortText] = Field(default_factory=list, max_length=_MAX_IDS)
    allergy_terms: list[ShortText] = Field(default_factory=list, max_length=_MAX_IDS)
    allergy_groups: list[ShortText] = Field(default_factory=list, max_length=_MAX_IDS)
    budget_rm_per_kg: Number | None = None
    preferred_location_id: str | None = Field(default=None, max_length=_MAX_TEXT)
    consent_allergies: bool = False
    consent_location: bool = False


class PreferencesOut(_Out):
    preferred_species_ids: list[str]
    allergy_species_ids: list[str]
    allergy_terms: list[str]
    allergy_groups: list[str]
    budget_rm_per_kg: float | None
    budget_source_label: Literal["Input pengguna"] = "Input pengguna"
    preferred_location_id: str | None


class ProfileOut(_Out):
    user_id: str
    has_profile: bool
    consent_allergies: bool
    consent_location: bool
    consent_allergies_at: datetime | None
    consent_location_at: datetime | None
    preferences: PreferencesOut
    session_notice: str
    message: str | None = None


class ConsentWithdrawIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["allergies", "location"]


class DeletedOut(_Out):
    deleted: bool
    message: str
