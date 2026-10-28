"""Public API routes with the memory backend (demo data) and fake repositories."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from hargaikan.core.search_decision import ScoredMatch
from hargaikan.core.verdict import ICON_ABOVE, ICON_BELOW, ICON_WITHIN
from hargaikan.domain import messages as m
from hargaikan.domain.models import PricePrediction
from tests.api_support import (
    API,
    CHOW_KIT,
    KL,
    PUDU,
    SP_A,
    SP_B,
    SP_OLD,
    all_strings,
    fake_client,
    fake_repositories,
    ids_by_name,
    memory_client,
)
from tests.fakes import FakePredictionRepository, FakePriceRepository

LABELS = {"Harga sejarah", "Input pengguna", "Anggaran model", "Anggaran perjalanan", "Jadual tambang"}


@pytest.fixture(scope="module")
def mem():  # type: ignore[no-untyped-def]
    with memory_client() as client:
        yield client


@pytest.fixture(scope="module")
def mem_ids(mem):  # type: ignore[no-untyped-def]
    return ids_by_name(mem)


# --- system ----------------------------------------------------------------------------------


def test_health(mem) -> None:  # type: ignore[no-untyped-def]
    body = mem.get(f"{API}/health").json()
    assert body == {"status": "ok", "backend": "memory", "demo": True, "version": "2.0.0"}


def test_capabilities_report_deferred_integrations(mem) -> None:  # type: ignore[no-untyped-def]
    body = mem.get(f"{API}/capabilities").json()
    assert body["backend"] == "memory" and body["demo"] is True
    assert body["supabase"] is False and body["pinecone"] is False
    assert body["weather"] is False and body["routing"] is False and body["transit_api"] is False
    assert body["transit_fare_table"] == {"connected": False, "entries": 0, "note": None}
    assert body["forecast_model"]["available"] is False
    notes = {x["name"]: x["note"] for x in body["integrations"]}
    assert notes["weather"] == m.WEATHER_NOT_CONNECTED and notes["routing"] == m.ROUTING_NOT_CONNECTED


def test_unknown_route_has_bm_error(mem) -> None:  # type: ignore[no-untyped-def]
    resp = mem.get(f"{API}/nope")
    assert resp.status_code == 404
    assert resp.json() == {"detail": {"code": "not_found", "messages": [m.RESOURCE_NOT_FOUND]}}


def test_cors_allows_configured_origin_only(mem) -> None:  # type: ignore[no-untyped-def]
    ok = mem.get(f"{API}/health", headers={"Origin": "http://localhost:5173"})
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:5173"
    other = mem.get(f"{API}/health", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in other.headers


# --- catalog ---------------------------------------------------------------------------------


def test_species_and_locations(mem) -> None:  # type: ignore[no-untyped-def]
    sp = mem.get(f"{API}/species").json()
    assert sp["count"] == 10 and sp["demo"] is True
    kembung = next(x for x in sp["items"] if x["code"] == "IKAN_KEMBUNG")
    assert any(a["alias"] == "IKAN KEMBUNG" for a in kembung["aliases"])
    one = mem.get(f"{API}/species/{kembung['id']}").json()
    assert one["name_ms"] == "IKAN KEMBUNG"
    locs = mem.get(f"{API}/locations").json()["items"]
    names = {x["name"]: x for x in locs}
    assert names["Kuala Lumpur"]["level"] == "negeri" and names["Pasar Chow Kit"]["parent_id"] == names["Kuala Lumpur"]["id"]


def test_unknown_species_is_404(mem) -> None:  # type: ignore[no-untyped-def]
    resp = mem.get(f"{API}/species/does-not-exist")
    assert resp.status_code == 404 and resp.json()["detail"]["messages"] == [m.UNKNOWN_SPECIES]


def test_inactive_species_hidden() -> None:
    with fake_client() as c:
        ids = {x["id"] for x in c.get(f"{API}/species").json()["items"]}
        assert SP_OLD.id not in ids and {SP_A.id, SP_B.id} <= ids
        found = c.get(f"{API}/search", params={"q": "ikan lama"}).json()
        assert found["backend"] == "fuzzy" and SP_OLD.id not in {x["species_id"] for x in found["matches"]}


# --- search ----------------------------------------------------------------------------------


def test_search_exact(mem) -> None:  # type: ignore[no-untyped-def]
    body = mem.get(f"{API}/search", params={"q": "  ikan   KEMBUNG "}).json()
    assert body["status"] == "confident" and body["backend"] == "exact"
    assert [x["name_ms"] for x in body["matches"]] == ["IKAN KEMBUNG"]


def test_search_fuzzy_typo(mem) -> None:  # type: ignore[no-untyped-def]
    body = mem.get(f"{API}/search", params={"q": "kembong"}).json()
    assert body["backend"] == "fuzzy" and body["status"] in ("confident", "candidates")
    assert body["matches"][0]["name_ms"] == "IKAN KEMBUNG"
    if body["status"] == "candidates":
        assert body["confirm_prompt"] == m.SEARCH_CONFIRM


def test_search_empty_and_not_found(mem) -> None:  # type: ignore[no-untyped-def]
    empty = mem.get(f"{API}/search", params={"q": "   "}).json()
    assert empty["status"] == "empty_query" and empty["notice"] == m.SEARCH_EMPTY and empty["matches"] == []
    none = mem.get(f"{API}/search", params={"q": "zzqxw"}).json()
    assert none["status"] == "none" and none["notice"] == m.SEARCH_NOT_FOUND and none["matches"] == []


class _FailingVector:
    def query(self, text: str, top_k: int, timeout_s: float) -> list[ScoredMatch]:
        raise TimeoutError("pinecone")


class _StaticVector:
    def query(self, text: str, top_k: int, timeout_s: float) -> list[ScoredMatch]:
        return [ScoredMatch(SP_OLD.id, "x", "lama", 0.99), ScoredMatch(SP_B.id, "metadata name", "ikan b", 0.95)]


def test_vector_failure_falls_back_to_fuzzy() -> None:
    with fake_client(vector_search=_FailingVector()) as c:
        body = c.get(f"{API}/search", params={"q": "alfa"}).json()
        assert body["backend"] == "fuzzy" and body["degraded"] is True
        assert body["notice"].startswith(m.SEARCH_FALLBACK_NOTICE)
        assert c.get(f"{API}/capabilities").json()["pinecone"] is True


def test_vector_results_use_active_catalog_names() -> None:
    with fake_client(vector_search=_StaticVector()) as c:
        body = c.get(f"{API}/search", params={"q": "something"}).json()
        assert body["backend"] == "pinecone" and body["status"] == "confident"
        assert [(x["species_id"], x["name_ms"]) for x in body["matches"]] == [(SP_B.id, "IKAN B")]


# --- prices ----------------------------------------------------------------------------------


def _ref(c, **params):  # type: ignore[no-untyped-def]
    return c.get(f"{API}/prices/reference", params=params)


def test_reference_direct_band(mem, mem_ids) -> None:  # type: ignore[no-untyped-def]
    sp, loc = mem_ids
    body = _ref(mem, species_id=sp["IKAN KEMBUNG"], location_id=loc["Pasar Pudu"]).json()
    assert body["available"] is True and body["is_fallback"] is False and body["message"] is None
    assert body["used_location"]["name"] == "Pasar Pudu"
    b = body["band"]
    assert b["source_label"] == "Harga sejarah" and b["demo"] is True and b["demo_label"] == m.DEMO_DATA
    assert b["p25"] <= b["median"] <= b["p75"] and b["band_source"] == "SYNTHETIC"
    assert body["chart"]["location"]["name"] == "Pasar Pudu" and body["chart"]["points"]
    assert body["chart"]["source_label"] == "Harga sejarah" and body["demo"] is True


def test_reference_chow_kit_fallback(mem, mem_ids) -> None:  # type: ignore[no-untyped-def]
    sp, loc = mem_ids
    body = _ref(mem, species_id=sp["IKAN KEMBUNG"], location_id=loc["Pasar Chow Kit"]).json()
    assert body["is_fallback"] is True
    assert body["message"] == "Data khusus Pasar Chow Kit belum tersedia. Rujukan Kuala Lumpur digunakan."
    assert body["used_location"]["name"] == "Kuala Lumpur" and body["band"]["location"]["level"] == "negeri"
    assert body["chart"]["location"]["name"] == "Kuala Lumpur"


@pytest.mark.parametrize("quote", ["abc", "0", "-5", "nan", "inf"])
def test_reference_invalid_quote(mem, mem_ids, quote: str) -> None:  # type: ignore[no-untyped-def]
    sp, loc = mem_ids
    resp = _ref(mem, species_id=sp["IKAN KEMBUNG"], location_id=loc["Pasar Pudu"], quote=quote)
    assert resp.status_code == 200
    body = resp.json()
    assert body["quote_error"] == m.INVALID_PRICE and body["verdict"] is None and body["quote"] is None


def test_reference_verdicts_with_fakes() -> None:
    with fake_client() as c:
        cases = {
            "10": ("below", m.VERDICT_BELOW, ICON_BELOW),
            "RM20": ("within", m.VERDICT_WITHIN, ICON_WITHIN),
            "22.01": ("above", m.VERDICT_ABOVE, ICON_ABOVE),
        }
        for quote, (kind, text, icon) in cases.items():
            body = _ref(c, species_id=SP_A.id, location_id=PUDU.id, quote=quote).json()
            assert body["verdict"] == {"kind": kind, "text": text, "icon": icon}
            assert body["quote"]["source_label"] == "Input pengguna" and body["quote"]["demo"] is False
            assert body["band"]["demo"] is False and body["band"]["demo_label"] is None
            assert body["chart"]["quote"]["value"] == pytest.approx(float(quote.replace("RM", "")))


def test_reference_no_data_shows_no_numbers() -> None:
    repos = fake_repositories(prices=FakePriceRepository())
    with fake_client(repos) as c:
        body = _ref(c, species_id=SP_A.id, location_id=PUDU.id, quote="10").json()
        assert body["available"] is False and body["band"] is None and body["chart"] is None
        assert body["message"] == m.NO_DATA
        assert body["verdict"]["text"] == m.NO_DATA and body["verdict"]["kind"] == "no_data"


def test_reference_unknown_location_and_missing_params(mem, mem_ids) -> None:  # type: ignore[no-untyped-def]
    sp, _ = mem_ids
    resp = _ref(mem, species_id=sp["IKAN KEMBUNG"], location_id="nowhere")
    assert resp.status_code == 404 and resp.json()["detail"]["messages"] == [m.UNKNOWN_LOCATION]
    resp = mem.get(f"{API}/prices/reference")
    assert resp.status_code == 422 and resp.json()["detail"]["code"] == "invalid_request"
    assert resp.json()["detail"]["messages"] == [m.INVALID_REQUEST]


# --- recommendations -------------------------------------------------------------------------


def test_recommendations_drafts_give_none(mem, mem_ids) -> None:  # type: ignore[no-untyped-def]
    sp, loc = mem_ids
    body = mem.get(f"{API}/recommendations", params={"species_id": sp["IKAN BAWAL HITAM"], "location_id": loc["Pasar Pudu"]}).json()
    assert body["items"] == [] and body["empty_message"] == "Tiada cadangan"
    assert body["disclaimer"] == (
        "Cadangan ini bukan nasihat perubatan. Jika anda mempunyai alergi makanan, semak nama spesies, "
        "label produk dan risiko pencemaran silang dengan penjual atau profesional kesihatan."
    )
    assert body["personalised"] is False


def test_recommendations_verified_edge_with_fakes() -> None:
    with fake_client() as c:
        body = c.get(f"{API}/recommendations", params={"species_id": SP_A.id, "location_id": PUDU.id}).json()
        assert [x["species"]["id"] for x in body["items"]] == [SP_B.id]
        item = body["items"][0]
        assert item["savings_rm_per_kg"] == 5.0 and item["location_name"] == "Pasar Pudu"
        assert item["source_label"] == "Harga sejarah" and item["demo"] is False
        assert body["disclaimer"] == m.RECOMMENDATION_DISCLAIMER
        # Same selected location for the candidate: at Chow Kit both fall back to KL.
        kl = c.get(f"{API}/recommendations", params={"species_id": SP_A.id, "location_id": CHOW_KIT.id}).json()
        assert kl["items"][0]["location_name"] == "Kuala Lumpur" and kl["items"][0]["savings_rm_per_kg"] == 5.0


# --- forecast --------------------------------------------------------------------------------


def test_forecast_unavailable(mem, mem_ids) -> None:  # type: ignore[no-untyped-def]
    sp, loc = mem_ids
    body = mem.get(f"{API}/forecast", params={"species_id": sp["IKAN KEMBUNG"], "location_id": loc["Pasar Pudu"]}).json()
    assert body["available"] is False and body["message"] == m.FORECAST_UNAVAILABLE and body["prediction"] is None


def test_forecast_available_with_published_prediction() -> None:
    pred = PricePrediction(
        SP_A.id, KL.id, date(2026, 1, 19), date(2026, 1, 25), 21.5, 17.0, 24.0, "random_forest", "rf-1", 0.8,
        date(2026, 1, 18), date(2026, 1, 31), "live",
    )
    repos = fake_repositories(predictions=FakePredictionRepository([pred]))
    with fake_client(repos, today=lambda: date(2026, 1, 20)) as c:
        body = c.get(f"{API}/forecast", params={"species_id": SP_A.id, "location_id": CHOW_KIT.id, "quote": "15"}).json()
        assert body["available"] is True
        p = body["prediction"]
        assert p["source_label"] == "Anggaran model" and p["location"]["name"] == "Kuala Lumpur" and p["p90"] == 24.0
        assert body["p75_message"] == m.FORECAST_MAY_EXCEED_P75
        assert body["early_buy_message"] is not None and body["quote"]["source_label"] == "Input pengguna"
    with fake_client(repos, today=lambda: date(2026, 3, 1)) as c:  # past valid_until
        body = c.get(f"{API}/forecast", params={"species_id": SP_A.id, "location_id": KL.id}).json()
        assert body["available"] is False and body["prediction"] is None


# --- travel ----------------------------------------------------------------------------------


def _travel(c, **body):  # type: ignore[no-untyped-def]
    return c.post(f"{API}/travel/estimate", json=body)


def test_travel_spec_example(mem) -> None:  # type: ignore[no-untyped-def]
    # fuel 20 km / 10 km/l × RM2.50 = RM5 + parking RM3 = RM8; toll RM6; one-way.
    body = _travel(
        mem, mode="kereta", distance_km=20, fuel_efficiency_km_per_l="10", fuel_price_rm_per_l="2.50",
        toll_rm="RM6.00", parking_rm=3, fish_price_rm_per_kg=18, qty_kg="2",
    ).json()
    assert body["fish_cost"]["value"] == "36.00"
    assert body["travel_total"]["value"] == "14.00"
    assert body["grand_total"]["value"] == "50.00"
    assert body["effective_rm_per_kg"]["value"] == "25.00"
    assert body["travel_total"]["display_label"] == "Anggaran perjalanan · Input pengguna"
    assert {x["source_label"] for x in body["inputs"]} == {"Input pengguna"}
    assert all(x["source_label"] in LABELS for x in [*body["inputs"], *body["components"]])
    assert m.TRAVEL_SEPARATE_NOTE in body["notes"] and body["demo"] is False


def test_travel_missing_inputs(mem) -> None:  # type: ignore[no-untyped-def]
    body = _travel(mem, mode="kereta", distance_km=10, fish_price_rm_per_kg=18, qty_kg=2).json()
    assert body["estimated"] is False and body["message"] == "Kos perjalanan belum dianggarkan"
    assert body["fish_cost"]["value"] == "36.00"
    assert body["grand_total"]["value"] is None and body["grand_total"]["display"] == m.NOT_COMPUTED
    assert body["effective_rm_per_kg"]["display"] == m.NOT_COMPUTED
    no_qty = _travel(mem, mode="pengangkutan_awam", transit_fare_rm=2).json()
    assert no_qty["travel_total"]["value"] == "2.00" and no_qty["fish_cost"]["display"] == m.NOT_COMPUTED


def test_travel_field_errors(mem) -> None:  # type: ignore[no-untyped-def]
    resp = _travel(mem, mode="kereta", toll_rm=-1, fuel_efficiency_km_per_l=0, qty_kg="abc")
    assert resp.status_code == 400
    fields = resp.json()["detail"]["fields"]
    assert set(fields) == {"toll_rm", "fuel_efficiency_km_per_l", "qty_kg"}
    assert _travel(mem, mode="basikal").status_code == 422


def test_travel_user_fare_return_trip(mem) -> None:  # type: ignore[no-untyped-def]
    body = _travel(mem, mode="pengangkutan_awam", transit_fare_rm="2.50", return_trip=True).json()
    assert body["travel_total"]["value"] == "5.00" and body["fare_source"] == "Input pengguna"


def test_travel_fare_table_lookup(tmp_path: Path) -> None:
    from hargaikan.config import Settings

    table = tmp_path / "fares.yaml"
    table.write_text(
        "- mode: mrt\n  from_station: Stesen Ujian A\n  to_station: Stesen Ujian B\n"
        "  fare_rm: 2.4\n  source: Jadual ujian\n  updated_on: 2026-01-01\n",
        encoding="utf-8",
    )
    with memory_client(settings=Settings(data_backend="memory", transit_fare_table_path=str(table))) as c:
        body = _travel(c, mode="pengangkutan_awam", transit_mode="mrt", origin="stesen ujian b", destination="Stesen Ujian A").json()
        assert body["estimated"] is True and body["travel_total"]["value"] == "2.40"
        assert body["fare_source"] == "Jadual tambang" and body["fare_source_detail"] == "Jadual ujian (2026-01-01)"
        fare = next(x for x in body["inputs"] if x["key"] == "transit_fare_rm")
        assert fare["source_label"] == "Jadual tambang"
        assert body["travel_total"]["display_label"] == m.TRAVEL_COMPUTED_FARE_TABLE_LABEL
        assert c.get(f"{API}/capabilities").json()["transit_fare_table"]["entries"] == 1
        other = _travel(c, mode="pengangkutan_awam", transit_mode="lrt", origin="Stesen Ujian A", destination="Stesen Ujian B").json()
        assert other["estimated"] is False and other["message"] == m.TRAVEL_NOT_ESTIMATED


def test_travel_no_fare_not_estimated(mem) -> None:  # type: ignore[no-untyped-def]
    body = _travel(mem, mode="pengangkutan_awam", transit_mode="mrt", origin="A", destination="B").json()
    assert body["estimated"] is False and body["message"] == m.TRAVEL_NOT_ESTIMATED and body["fare_source"] is None


def test_travel_invalid_fare_table_not_used(tmp_path: Path) -> None:
    from hargaikan.config import Settings

    table = tmp_path / "bad.yaml"
    table.write_text("- mode: mrt\n  fare_rm: -1\n", encoding="utf-8")
    with memory_client(settings=Settings(data_backend="memory", transit_fare_table_path=str(table))) as c:
        body = _travel(c, mode="pengangkutan_awam", transit_mode="mrt", origin="A", destination="B").json()
        assert body["estimated"] is False and m.FARE_TABLE_INVALID in body["notes"]
        assert c.get(f"{API}/capabilities").json()["transit_fare_table"]["note"] == m.FARE_TABLE_INVALID


# --- overview and forbidden phrases ----------------------------------------------------------


def test_overview(mem) -> None:  # type: ignore[no-untyped-def]
    body = mem.get(f"{API}/overview").json()
    assert body["species_count"] == 10 and body["premise_count"] == 8 and body["demo"] is True
    assert [x["label"] for x in body["legend"]] == ["Harga sejarah", "Input pengguna", "Anggaran model", "Anggaran perjalanan"]
    assert body["suggestions"] and all(x["source_label"] == "Harga sejarah" and x["demo"] for x in body["suggestions"])
    counts = [x["record_count"] for x in body["suggestions"]]
    assert counts == sorted(counts, reverse=True)


def test_no_response_contains_forbidden_phrase(mem, mem_ids) -> None:  # type: ignore[no-untyped-def]
    sp, loc = mem_ids
    texts: list[str] = []
    for path, params in [
        ("/species", {}),
        ("/locations", {}),
        ("/overview", {}),
        ("/capabilities", {}),
        ("/search", {"q": "kembong"}),
        ("/search", {"q": "zzz"}),
    ]:
        texts += all_strings(mem.get(f"{API}{path}", params=params).json())
    for name in sp:
        for lname in ("Pasar Chow Kit", "Pasar Pudu"):
            q = {"species_id": sp[name], "location_id": loc[lname]}
            texts += all_strings(mem.get(f"{API}/prices/reference", params={**q, "quote": "20"}).json())
            texts += all_strings(mem.get(f"{API}/recommendations", params=q).json())
            texts += all_strings(mem.get(f"{API}/forecast", params=q).json())
    texts += all_strings(_travel(mem, mode="kereta", toll_rm=-1).json())
    lowered = [t.lower() for t in texts]
    bad = [p for p in m.FORBIDDEN_PHRASES if any(p.lower() in t for t in lowered)]
    assert not bad
