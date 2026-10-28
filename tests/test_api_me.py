"""Authenticated /me routes with demo sessions: profile, consent, delete, isolation, dashboard."""

from __future__ import annotations

import pytest

from hargaikan.domain import messages as m
from hargaikan.repositories.memory_repo import MemoryUserStore
from tests.api_support import API, SP_A, demo_headers, fake_client, fake_repositories, ids_by_name, memory_client
from tests.fakes import FailingPriceRepository


@pytest.fixture()
def store() -> MemoryUserStore:
    return MemoryUserStore()


@pytest.fixture()
def client(store):  # type: ignore[no-untyped-def]
    with memory_client(store) as c:
        yield c


def _profile(c, headers, **body):  # type: ignore[no-untyped-def]
    return c.put(f"{API}/me/profile", json=body, headers=headers)


def test_me_requires_token(client) -> None:  # type: ignore[no-untyped-def]
    for method, path in [("get", "/me"), ("get", "/me/profile"), ("put", "/me/profile"), ("delete", "/me"), ("get", "/me/dashboard")]:
        resp = getattr(client, method)(f"{API}{path}", **({"json": {}} if method == "put" else {}))
        assert resp.status_code == 401, path
        assert resp.json() == {"detail": {"code": "auth_required", "messages": [m.AUTH_REQUIRED]}}


def test_bad_and_tampered_tokens_rejected(client) -> None:  # type: ignore[no-untyped-def]
    headers = demo_headers(client)
    token = headers["Authorization"].split()[1]
    for bad in ["garbage", token[:-2] + "xx", "demo.not-a-uuid.sig", token + ".extra"]:
        assert client.get(f"{API}/me", headers={"Authorization": f"Bearer {bad}"}).status_code == 401
    # A token signed by another process secret is not accepted.
    with memory_client() as other:
        assert other.get(f"{API}/me", headers=headers).status_code == 401


def test_demo_session_and_me(client) -> None:  # type: ignore[no-untyped-def]
    resp = client.post(f"{API}/session/demo").json()
    assert resp["token_type"] == "bearer" and resp["access_token"].startswith("demo.") and resp["notice"] == m.SESSION_NOTICE
    me = client.get(f"{API}/me", headers={"Authorization": f"Bearer {resp['access_token']}"}).json()
    assert me == {"user_id": resp["user_id"], "backend": "memory", "demo": True, "session_notice": m.SESSION_NOTICE}


def test_allergy_without_consent_rejected_and_nothing_stored(client, store) -> None:  # type: ignore[no-untyped-def]
    h = demo_headers(client)
    sp, loc = ids_by_name(client)
    resp = _profile(client, h, preferred_species_ids=[sp["IKAN KEMBUNG"]], allergy_terms=["udang"], budget_rm_per_kg="12")
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "consent_required"
    assert m.CONSENT_REQUIRED_ALLERGY in resp.json()["detail"]["messages"]
    resp = _profile(client, h, preferred_location_id=loc["Pasar Pudu"])
    assert resp.status_code == 400 and resp.json()["detail"]["messages"] == [m.CONSENT_REQUIRED_LOCATION]
    body = client.get(f"{API}/me/profile", headers=h).json()
    assert body["has_profile"] is False and body["preferences"]["preferred_species_ids"] == []
    assert store.profiles == {} and store.preferences == {}


def test_invalid_budget_and_unknown_ids(client) -> None:  # type: ignore[no-untyped-def]
    h = demo_headers(client)
    resp = _profile(client, h, budget_rm_per_kg="-3", preferred_species_ids=["nope"])
    assert resp.status_code == 400
    msgs = resp.json()["detail"]["messages"]
    assert "Bajet mesti nombor antara RM0.01 dan RM1000/kg." in msgs and m.UNKNOWN_SPECIES in msgs


def test_profile_round_trip_withdraw_and_delete(client) -> None:  # type: ignore[no-untyped-def]
    h = demo_headers(client)
    sp, loc = ids_by_name(client)
    resp = _profile(
        client, h,
        preferred_species_ids=[sp["IKAN KEMBUNG"]], allergy_terms=["udang"], allergy_groups=["krustasea"],
        budget_rm_per_kg="12.50", preferred_location_id=loc["Pasar Pudu"], consent_allergies=True, consent_location=True,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["message"] == m.PROFILE_SAVED
    body = client.get(f"{API}/me/profile", headers=h).json()
    assert body["has_profile"] and body["consent_allergies"] and body["consent_allergies_at"] is not None
    prefs = body["preferences"]
    assert prefs["allergy_terms"] == ["udang"] and prefs["budget_rm_per_kg"] == 12.5
    assert prefs["preferred_location_id"] == loc["Pasar Pudu"] and prefs["budget_source_label"] == "Input pengguna"

    resp = client.post(f"{API}/me/consent/withdraw", json={"kind": "allergies"}, headers=h).json()
    assert resp["message"] == m.CONSENT_WITHDRAWN_ALLERGIES and resp["consent_allergies"] is False
    assert resp["preferences"]["allergy_terms"] == [] and resp["preferences"]["allergy_groups"] == []
    assert resp["preferences"]["preferred_location_id"] == loc["Pasar Pudu"] and resp["consent_allergies_at"] is None

    resp = client.post(f"{API}/me/consent/withdraw", json={"kind": "location"}, headers=h).json()
    assert resp["preferences"]["preferred_location_id"] is None and resp["consent_location"] is False
    assert client.post(f"{API}/me/consent/withdraw", json={"kind": "other"}, headers=h).status_code == 422

    deleted = client.delete(f"{API}/me", headers=h).json()
    assert deleted == {"deleted": True, "message": m.DATA_DELETED}
    body = client.get(f"{API}/me/profile", headers=h).json()
    assert body["has_profile"] is False and body["preferences"]["preferred_species_ids"] == []


def test_users_are_isolated(client) -> None:  # type: ignore[no-untyped-def]
    a, b = demo_headers(client), demo_headers(client)
    sp, _ = ids_by_name(client)
    assert _profile(client, a, preferred_species_ids=[sp["IKAN SELAR"]]).status_code == 200
    body_b = client.get(f"{API}/me/profile", headers=b).json()
    assert body_b["has_profile"] is False and body_b["preferences"]["preferred_species_ids"] == []
    client.delete(f"{API}/me", headers=b)
    assert client.get(f"{API}/me/profile", headers=a).json()["preferences"]["preferred_species_ids"] == [sp["IKAN SELAR"]]


def test_recommendations_use_caller_allergies(client) -> None:  # type: ignore[no-untyped-def]
    h = demo_headers(client)
    sp, loc = ids_by_name(client)
    _profile(client, h, allergy_terms=["kembung"], consent_allergies=True)
    q = {"species_id": sp["IKAN KEMBUNG"], "location_id": loc["Pasar Pudu"]}
    personal = client.get(f"{API}/recommendations", params=q, headers=h).json()
    assert personal["personalised"] is True and personal["allergy_caution"] == m.ALLERGY_CAUTION
    public = client.get(f"{API}/recommendations", params=q).json()
    assert public["personalised"] is False and public["allergy_caution"] is None
    invalid = client.get(f"{API}/recommendations", params=q, headers={"Authorization": "Bearer nope"})
    assert invalid.status_code == 200 and invalid.json()["personalised"] is False


def test_dashboard_empty_profile(client) -> None:  # type: ignore[no-untyped-def]
    h = demo_headers(client)
    body = client.get(f"{API}/me/dashboard", headers=h).json()
    assert body["state"] == "empty" and body["message"] == m.EMPTY_PROFILE and body["items"] == []
    ov = body["overview"]
    assert ov["species_count"] == 10 and ov["suggestions"]
    assert all(x["source_label"] == "Harga sejarah" and x["location"]["name"] == "Kuala Lumpur" for x in ov["suggestions"])


def test_dashboard_personalised(client) -> None:  # type: ignore[no-untyped-def]
    h = demo_headers(client)
    sp, loc = ids_by_name(client)
    _profile(client, h, preferred_species_ids=[sp["IKAN KEMBUNG"], sp["IKAN SELAR"]], budget_rm_per_kg="30")
    body = client.get(f"{API}/me/dashboard", headers=h).json()
    assert body["state"] == "personalised" and body["location"]["name"] == "Kuala Lumpur"
    assert body["location_note"] == m.DASHBOARD_DEFAULT_LOCATION
    assert [x["species"]["name_ms"] for x in body["items"]] == ["IKAN KEMBUNG", "IKAN SELAR"]
    for item in body["items"]:
        band = item["reference"]["band"]
        assert band["source_label"] == "Harga sejarah" and band["demo"] is True
        expected = m.WITHIN_BUDGET if band["median"] <= 30 else m.OVER_BUDGET
        assert item["budget"]["text"] == expected and item["budget"]["budget_source_label"] == "Input pengguna"
        assert item["recommendations"]["disclaimer"] == m.RECOMMENDATION_DISCLAIMER
        assert item["forecast"]["message"] == m.FORECAST_UNAVAILABLE

    _profile(client, h, preferred_species_ids=[sp["IKAN KEMBUNG"]], preferred_location_id=loc["Pasar Chow Kit"], consent_location=True)
    body = client.get(f"{API}/me/dashboard", headers=h).json()
    assert body["location"]["name"] == "Pasar Chow Kit" and body["location_note"] is None
    assert body["items"][0]["reference"]["message"] == "Data khusus Pasar Chow Kit belum tersedia. Rujukan Kuala Lumpur digunakan."
    assert body["items"][0]["budget"] is None


def test_dashboard_isolates_failing_sections() -> None:
    repos = fake_repositories(prices=FailingPriceRepository(), backend="memory")
    with fake_client(repos) as c:
        h = demo_headers(c)
        assert _profile(c, h, preferred_species_ids=[SP_A.id]).status_code == 200
        resp = c.get(f"{API}/me/dashboard", headers=h)
        assert resp.status_code == 200
        item = resp.json()["items"][0]
        assert item["reference"] is None and item["reference_notice"] == m.SECTION_UNAVAILABLE
        assert item["recommendations_notice"] == m.SECTION_UNAVAILABLE and item["forecast_notice"] == m.SECTION_UNAVAILABLE
        empty = demo_headers(c)
        ov = c.get(f"{API}/me/dashboard", headers=empty).json()["overview"]
        assert ov["notice"] == m.SECTION_UNAVAILABLE and ov["suggestions_notice"] == m.SECTION_UNAVAILABLE
