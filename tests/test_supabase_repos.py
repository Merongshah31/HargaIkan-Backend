"""Supabase repositories against a fake client: row mapping, live filters and the user token."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from hargaikan.domain.models import Location, PriceBand, UserPreferences, UserProfile
from hargaikan.pipeline.locations import PremiseLocation
from hargaikan.repositories.catalog_repo import SupabaseCatalogRepository
from hargaikan.repositories.prediction_repo import SupabasePredictionRepository
from hargaikan.repositories.price_repo import SupabasePriceRepository
from hargaikan.repositories.protocols import (
    CatalogRepository,
    PredictionRepository,
    PriceRepository,
    UserRepository,
)
from hargaikan.repositories.user_repo import USER_TABLES, SupabaseUserRepository
from tests.supabase_fakes import FakeClient

SP = "11111111-1111-1111-1111-111111111111"
SP2 = "22222222-2222-2222-2222-222222222222"

LOCATIONS = [
    {"id": "MY", "name": "Malaysia", "level": "nasional", "parent_id": None, "lat": None, "lng": None, "chain": None, "branch": None},
    {"id": "KL", "name": "Kuala Lumpur", "level": "negeri", "parent_id": "MY", "lat": None, "lng": None, "chain": None, "branch": None},
    {"id": "P1", "name": "Pasar TTDI", "level": "pasar", "parent_id": "KL", "lat": 3.14, "lng": "101.63", "chain": None, "branch": None},
    {"id": "P2", "name": "MYDIN SUBANG", "level": "premis", "parent_id": "KL", "lat": None, "lng": None, "chain": "MYDIN", "branch": "SUBANG"},
]


def _band(id_: str, status: str, loc: str = "KL", source: str = "IKANSIGHT", end: str = "2025-06-15") -> dict:
    return {
        "id": id_, "species_id": SP, "location_id": loc, "grade": None, "p25": "10.5", "median": 12, "p75": "14.25",
        "record_count": 7, "confidence_level": "sederhana", "period_start": "2025-06-02", "period_end": end,
        "band_source": source, "publish_status": status,
    }


def _record(id_: str, loc: str, price: str, count: int, date_end: str, status: str = "live", q: str = "ok") -> dict:
    return {
        "id": id_, "species_id": SP, "location_id": loc, "price_rm_per_kg": price, "record_count": count,
        "date_end": date_end, "publish_status": status, "quality_status": q,
    }


# --- catalog --------------------------------------------------------------------------------------


def test_catalog_mapping() -> None:
    client = FakeClient({
        "species": [
            {"id": SP, "code": "IKAN_A", "canonical_name_ms": "IKAN A", "canonical_name_en": None,
             "allergen_groups": ["ikan_bersirip"], "active": True},
            {"id": SP2, "code": "IKAN_B", "canonical_name_ms": "IKAN B", "canonical_name_en": "Fish B",
             "allergen_groups": None, "active": False},
        ],
        "species_aliases": [{"id": "a1", "species_id": SP, "alias": "Ikan A", "language": "ms"}],
        "locations": LOCATIONS,
        "fish_profiles": [
            {"species_id": SP, "texture": "lembut", "taste": None, "bone_level": "sedikit",
             "cooking_methods": ["goreng", "stim"], "allergen_groups": ["ikan_bersirip"], "notes": None},
            {"species_id": "unknown", "texture": "x", "taste": "x", "bone_level": "x", "cooking_methods": [],
             "allergen_groups": [], "notes": None},
        ],
        "fish_similarity": [
            {"from_species_id": SP, "to_species_id": SP2, "similarity_score": "0.8", "reason": "Sama.",
             "basis": ["tekstur"], "verified": False},
            {"from_species_id": SP2, "to_species_id": SP, "similarity_score": 0.7, "reason": "Sama.",
             "basis": ["rasa"], "verified": True},
        ],
    })
    repo = SupabaseCatalogRepository(client)
    assert isinstance(repo, CatalogRepository)

    active = repo.list_species()
    assert [s.code for s in active] == ["IKAN_A"] and active[0].allergen_groups == ("ikan_bersirip",)
    assert client.queries_for("species")[-1].has("eq", "active", True)
    every = repo.list_species(active_only=False)
    assert {s.code: s.allergen_groups for s in every} == {"IKAN_A": ("ikan_bersirip",), "IKAN_B": ()}

    assert repo.list_aliases()[0].language == "ms"

    locs = {loc.id: loc for loc in repo.list_locations()}
    assert isinstance(locs["P2"], PremiseLocation) and locs["P2"].chain == "MYDIN"
    assert type(locs["P1"]) is Location and locs["P1"].lng == 101.63 and locs["MY"].parent_id is None

    profiles = repo.list_profiles()
    assert len(profiles) == 1
    p = profiles[0]
    assert (p.species_code, p.taste, p.cooking_methods) == ("IKAN_A", "", ("goreng", "stim"))

    edges = repo.list_similarity(SP)
    assert len(edges) == 1 and edges[0].similarity_score == 0.8 and edges[0].basis == ("tekstur",)
    assert edges[0].verified is False


# --- prices ---------------------------------------------------------------------------------------


def test_bands_are_live_only_and_mapped() -> None:
    client = FakeClient({"price_bands": [_band("b1", "live"), _band("b2", "staged"), _band("b3", "retired")]})
    repo = SupabasePriceRepository(client)
    assert isinstance(repo, PriceRepository)
    bands = repo.bands_for_species(SP)
    assert bands == [
        PriceBand(SP, "KL", None, 10.5, 12.0, 14.25, 7, "sederhana", date(2025, 6, 2), date(2025, 6, 15), "IKANSIGHT")
    ]
    assert all(isinstance(x, float) for x in (bands[0].p25, bands[0].median, bands[0].p75))
    q = client.queries_for("price_bands")[0]
    assert q.has("eq", "publish_status", "live") and q.has("eq", "species_id", SP)


def _series_client() -> FakeClient:
    return FakeClient({
        "locations": LOCATIONS,
        "price_records": [
            _record("r1", "P1", "10", 1, "2025-06-08"),
            _record("r2", "P2", "14", 3, "2025-06-08"),
            _record("r3", "P1", "12", 1, "2025-06-15"),
            _record("r4", "P2", "20", 2, "2025-06-15"),
            _record("r5", "P1", "99", 9, "2025-06-15", status="staged"),
            _record("r6", "P1", "99", 9, "2025-06-15", q="outlier"),
        ],
    })


def test_weekly_series_for_ancestor_uses_descendants() -> None:
    client = _series_client()
    series = SupabasePriceRepository(client).weekly_series(SP, "KL")
    assert series == [(date(2025, 6, 2), 14.0, 4), (date(2025, 6, 9), 20.0, 3)]
    q = client.queries_for("price_records")[0]
    assert q.has("eq", "publish_status", "live") and q.has("eq", "quality_status", "ok")
    assert not any(n == "eq" and a[0] == "location_id" for n, a, _ in q.calls)


def test_weekly_series_for_leaf_filters_location_in_query() -> None:
    client = _series_client()
    assert SupabasePriceRepository(client).weekly_series(SP, "P1") == [(date(2025, 6, 2), 10.0, 1), (date(2025, 6, 9), 12.0, 1)]
    q = client.queries_for("price_records")[0]
    assert q.has("eq", "location_id", "P1") and q.has("eq", "publish_status", "live")


def test_weekly_series_pages_through_results() -> None:
    rows = [_record(f"r{i:05d}", "P1", "10", 1, "2025-06-08") for i in range(2500)]
    client = FakeClient({"locations": LOCATIONS, "price_records": rows})
    assert SupabasePriceRepository(client).weekly_series(SP, "P1") == [(date(2025, 6, 2), 10.0, 2500)]
    assert len(client.queries_for("price_records")) == 3


def test_catalog_overview() -> None:
    client = FakeClient({
        "species": [{"id": SP, "active": True}, {"id": SP2, "active": False}],
        "locations": LOCATIONS,
        "price_bands": [
            _band("b1", "live", end="2025-06-15"),
            _band("b2", "live", end="2025-06-22", source="SYNTHETIC"),
            _band("b3", "staged", end="2025-07-30"),
        ],
    })
    o = SupabasePriceRepository(client).catalog_overview()
    assert o == {"species_count": 1, "premise_count": 2, "latest_period": date(2025, 6, 22), "demo": True}
    assert all(q.has("eq", "publish_status", "live") for q in client.queries_for("price_bands"))


# --- predictions ----------------------------------------------------------------------------------


def _pred(start: str, status: str) -> dict:
    return {
        "id": start + status, "species_id": SP, "location_id": "KL", "target_start": start, "target_end": start,
        "predicted_median": "12.3", "p10": None, "p90": "15", "method": "baseline_median4", "model_version": "v1",
        "mae": "1.2", "last_data_date": "2025-06-15", "valid_until": "2025-06-29", "publish_status": status,
    }


def test_prediction_live_only_and_mapped() -> None:
    client = FakeClient({
        "price_predictions": [_pred("2025-06-16", "live"), _pred("2025-06-23", "live"), _pred("2025-06-30", "staged")],
        "model_metrics": [
            {"id": "m1", "model_version": "v1", "scope": "overall", "species_id": None, "location_id": None, "mae": "1.5",
             "rmse": "2", "baseline_mae": "1.7", "baseline_rmse": None, "interval_coverage": None, "n_test": "4",
             "decision": "publish", "publish_status": "live"},
            {"id": "m2", "model_version": "v1", "scope": "overall", "mae": "9", "decision": "x", "publish_status": "staged"},
        ],
    })
    repo = SupabasePredictionRepository(client)
    assert isinstance(repo, PredictionRepository)
    p = repo.live_prediction(SP, "KL")
    assert p is not None and p.target_start == date(2025, 6, 23) and p.predicted_median == 12.3
    assert p.p10 is None and p.p90 == 15.0 and p.valid_until == date(2025, 6, 29) and p.publish_status == "live"
    assert client.queries_for("price_predictions")[0].has("eq", "publish_status", "live")
    assert repo.live_prediction(SP, "P1") is None

    metrics = repo.metrics_for("v1")
    assert len(metrics) == 1 and metrics[0]["mae"] == 1.5 and metrics[0]["n_test"] == 4
    assert client.queries_for("model_metrics")[0].has("eq", "publish_status", "live")


# --- users ----------------------------------------------------------------------------------------

TOKEN = "user-access-token"


def test_user_repo_sends_user_token_on_every_request() -> None:
    client = FakeClient({
        "user_profiles": [{"user_id": "u1", "consent_allergies": True, "consent_allergies_at": "2025-06-01T08:00:00.123+00:00",
                           "consent_location": False, "consent_location_at": None}],
        "user_preferences": [{"user_id": "u1", "preferred_species_ids": [SP], "allergy_species_ids": None,
                              "allergy_terms": ["udang"], "allergy_groups": [], "budget_rm_per_kg": "25.5",
                              "preferred_location_id": None}],
    })
    repo = SupabaseUserRepository(client, "u1", TOKEN)
    assert isinstance(repo, UserRepository)
    assert TOKEN not in repr(repo)
    prof = repo.get_profile()
    assert prof == UserProfile("u1", True, False, datetime(2025, 6, 1, 8, 0, 0, 123000, tzinfo=timezone.utc), None)
    prefs = repo.get_preferences()
    assert prefs == UserPreferences("u1", (SP,), (), ("udang",), (), 25.5, None)
    repo.delete_all()
    assert client.postgrest.auth_calls and set(client.postgrest.auth_calls) == {TOKEN}
    assert client.queries and all(q.auth_header == f"Bearer {TOKEN}" for q in client.queries)
    assert all(q.has("eq", "user_id", "u1") for q in client.queries)


def test_user_repo_upserts_rows() -> None:
    client = FakeClient()
    repo = SupabaseUserRepository(client, "u1", TOKEN)
    at = datetime(2025, 6, 1, tzinfo=timezone.utc)
    repo.upsert_profile(UserProfile("u1", True, False, at, None))
    repo.upsert_preferences(UserPreferences("u1", preferred_species_ids=(SP,), budget_rm_per_kg=30))
    (prof_q,), (pref_q,) = client.queries_for("user_profiles"), client.queries_for("user_preferences")
    prof_row = prof_q.calls[0][1][0]
    assert prof_q.calls[0][2]["on_conflict"] == "user_id"
    assert prof_row["user_id"] == "u1" and prof_row["consent_allergies_at"] == at.isoformat() and "updated_at" in prof_row
    pref_row = pref_q.calls[0][1][0]
    assert pref_row["preferred_species_ids"] == [SP] and pref_row["budget_rm_per_kg"] == 30.0
    assert pref_row["allergy_terms"] == []


def test_user_repo_rejects_other_users_rows() -> None:
    repo = SupabaseUserRepository(FakeClient(), "u1", TOKEN)
    with pytest.raises(PermissionError):
        repo.upsert_profile(UserProfile("u2", False, False, None, None))
    with pytest.raises(PermissionError):
        repo.upsert_preferences(UserPreferences("u2"))


def test_user_repo_delete_all_order() -> None:
    client = FakeClient()
    SupabaseUserRepository(client, "u1", TOKEN).delete_all()
    assert [q.table for q in client.executed] == list(USER_TABLES)
    assert USER_TABLES == ("user_preferences", "purchase_history", "travel_estimates", "user_profiles")
    assert all(q.method() == "delete" and q.has("eq", "user_id", "u1") for q in client.executed)


def test_user_repo_requires_token_and_user() -> None:
    with pytest.raises(ValueError):
        SupabaseUserRepository(FakeClient(), "u1", "")
    with pytest.raises(ValueError):
        SupabaseUserRepository(FakeClient(), "", TOKEN)


@pytest.mark.filterwarnings("ignore::DeprecationWarning")  # supabase-py internals (timeout/verify)
def test_real_supabase_client_sends_user_token() -> None:
    """With the installed supabase-py, postgrest.auth sets the bearer header on table requests."""
    from supabase import create_client

    client = create_client("https://example.supabase.co", "anon-placeholder")
    repo = SupabaseUserRepository(client, "u1", TOKEN)
    builder = repo._table("user_profiles")
    assert builder.headers["Authorization"] == f"Bearer {TOKEN}"
    assert builder.headers.get("apikey", builder.headers.get("apiKey")) == "anon-placeholder"
