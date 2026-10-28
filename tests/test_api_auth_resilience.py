"""Supabase token verification, failure isolation and import hygiene of the API."""

from __future__ import annotations

import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx
import jwt
import pytest

from hargaikan.api.auth import AuthError, DemoTokens, SupabaseTokenVerifier
from hargaikan.config import Settings
from hargaikan.domain import messages as m
from hargaikan.repositories.memory_repo import ConsentRequiredError, MemoryUserStore
from hargaikan.services.profile_service import consent_message
from tests.api_support import API, PUDU, SP_A, fake_client, fake_repositories
from tests.fakes import FailingCatalogRepository, FailingPriceRepository

ROOT = Path(__file__).resolve().parents[1]
SECRET = "test-jwt-secret-for-unit-tests-only-0123456789"
SUPA = {"data_backend": "supabase", "supabase_url": "https://example.supabase.co", "supabase_anon_key": "anon-placeholder"}


def _token(secret: str = SECRET, **overrides) -> str:  # type: ignore[no-untyped-def]
    claims = {"sub": str(uuid.uuid4()), "aud": "authenticated", "role": "authenticated", "exp": int(time.time()) + 3600}
    claims.update(overrides)
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, secret, algorithm="HS256")


def _supabase_client(store: MemoryUserStore | None = None, **settings):  # type: ignore[no-untyped-def]
    s = Settings(**{**SUPA, "supabase_jwt_secret": SECRET, **settings})
    return fake_client(fake_repositories(backend="supabase"), store, settings=s)


# --- Supabase JWT ----------------------------------------------------------------------------


def test_hs256_token_verified_locally() -> None:
    with _supabase_client() as c:
        sub = str(uuid.uuid4())
        tok = _token(sub=sub)
        me = c.get(f"{API}/me", headers={"Authorization": f"Bearer {tok}"}).json()
        assert me["user_id"] == sub and me["backend"] == "supabase" and me["demo"] is False
        profile = c.get(f"{API}/me/profile", headers={"Authorization": f"Bearer {tok}"}).json()
        assert profile["user_id"] == sub and profile["has_profile"] is False


@pytest.mark.parametrize(
    "token",
    [
        _token(exp=int(time.time()) - 10),
        _token(aud="anon"),
        _token(secret="another-secret-that-is-long-enough-000000"),
        _token(sub=None),
        "not-a-jwt",
    ],
    ids=["expired", "wrong-audience", "wrong-secret", "no-sub", "garbage"],
)
def test_invalid_supabase_tokens_rejected(token: str) -> None:
    with _supabase_client() as c:
        resp = c.get(f"{API}/me", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 401 and resp.json()["detail"]["messages"] == [m.AUTH_REQUIRED]


def test_demo_session_only_on_memory_backend() -> None:
    with _supabase_client() as c:
        assert c.post(f"{API}/session/demo").status_code == 404


def test_remote_verification_and_cache() -> None:
    calls: list[httpx.Request] = []
    sub = str(uuid.uuid4())

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.headers["Authorization"] == "Bearer " + good:
            return httpx.Response(200, json={"id": sub})
        return httpx.Response(401, json={"msg": "invalid"})

    good = _token(secret="signed-by-supabase-not-known-locally-0000", sub=sub)
    s = Settings(**SUPA)  # no JWT secret → remote check
    verifier = SupabaseTokenVerifier(s, httpx.Client(transport=httpx.MockTransport(handler)))
    assert verifier.verify(good) == sub
    assert verifier.verify(good) == sub
    assert len(calls) == 1  # cached
    assert calls[0].url.path == "/auth/v1/user" and calls[0].headers["apikey"] == "anon-placeholder"
    with pytest.raises(AuthError):
        verifier.verify(_token(secret="other-secret-xxxxxxxxxxxxxxxxxxxxxxxxxx"))


def test_remote_verification_outage_is_503() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    with fake_client(
        fake_repositories(backend="supabase"),
        settings=Settings(**SUPA),
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    ) as c:
        resp = c.get(f"{API}/me", headers={"Authorization": f"Bearer {_token()}"})
        assert resp.status_code == 503 and resp.json()["detail"]["messages"] == [m.PROFILE_UNAVAILABLE]
        assert c.get(f"{API}/species").status_code == 200


def test_demo_tokens_with_fixed_secret_survive_restart() -> None:
    token, uid = DemoTokens("fixed-secret").issue()
    assert DemoTokens("fixed-secret").verify(token) == uid
    with pytest.raises(AuthError):
        DemoTokens("other").verify(token)


def test_supabase_consent_violation_maps_to_bm_message() -> None:
    class APIError(Exception):
        def __init__(self) -> None:
            self.code = "23514"
            self.message = "consent_location_required"
            super().__init__(self.message)

    assert consent_message(APIError()) == m.CONSENT_REQUIRED_LOCATION
    assert consent_message(ConsentRequiredError("consent_allergies_required")) == m.CONSENT_REQUIRED_ALLERGY
    assert consent_message(RuntimeError("timeout")) is None


# --- failure isolation -----------------------------------------------------------------------


def test_failing_prices_only_break_price_endpoints() -> None:
    with fake_client(fake_repositories(prices=FailingPriceRepository())) as c:
        resp = c.get(f"{API}/prices/reference", params={"species_id": SP_A.id, "location_id": PUDU.id})
        assert resp.status_code == 503
        assert resp.json() == {"detail": {"code": "section_unavailable", "messages": [m.SECTION_UNAVAILABLE]}}
        assert c.get(f"{API}/recommendations", params={"species_id": SP_A.id, "location_id": PUDU.id}).status_code == 503
        assert c.get(f"{API}/species").status_code == 200
        assert c.get(f"{API}/search", params={"q": "ikan a"}).status_code == 200
        assert c.post(f"{API}/travel/estimate", json={"mode": "pengangkutan_awam", "transit_fare_rm": 2}).status_code == 200
        overview = c.get(f"{API}/overview")
        assert overview.status_code == 200 and overview.json()["notice"] == m.SECTION_UNAVAILABLE


def test_failing_catalog_keeps_health_and_travel() -> None:
    with fake_client(fake_repositories(catalog=FailingCatalogRepository())) as c:
        assert c.get(f"{API}/species").status_code == 503
        assert c.get(f"{API}/search", params={"q": "x"}).status_code == 503
        assert c.get(f"{API}/health").status_code == 200
        assert c.get(f"{API}/capabilities").status_code == 200


def test_unbuildable_repositories_give_503_not_crash() -> None:
    from fastapi.testclient import TestClient

    from hargaikan.api.main import create_app

    with TestClient(create_app(Settings(data_backend="supabase"))) as c:  # no SUPABASE_URL / key
        assert c.get(f"{API}/health").json()["backend"] == "supabase"
        resp = c.get(f"{API}/species")
        assert resp.status_code == 503 and resp.json()["detail"]["messages"] == [m.SECTION_UNAVAILABLE]


# --- import hygiene --------------------------------------------------------------------------


def test_api_import_has_no_heavy_or_ui_modules() -> None:
    code = (
        "import sys\n"
        "import hargaikan.api.main\n"
        "from hargaikan.repositories.factory import demo_dataset\n"
        "demo_dataset()\n"
        "bad = [n for n in ('streamlit', 'torch', 'sentence_transformers', 'pinecone', 'sklearn') if n in sys.modules]\n"
        "print(bad)\n"
        "sys.exit(1 if bad else 0)\n"
    )
    env = {**os.environ, "DATA_BACKEND": "memory"}
    proc = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_services_do_not_import_fastapi() -> None:
    import ast

    for path in (ROOT / "src" / "hargaikan" / "services").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names = [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
        names += [n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module]
        assert not [x for x in names if x.split(".")[0] in ("fastapi", "starlette", "streamlit")], path.name
