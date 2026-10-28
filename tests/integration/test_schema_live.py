"""Live schema and RLS checks against the Supabase dev project (Requirements 2.2, 2.3, 2.4, 2.9, 2.10).

Skipped unless SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY and SUPABASE_ANON_KEY are available via
hargaikan.config.get_secret (a project-root .env is loaded first if present).

Re-applying the migrations (Requirement 2.9) cannot be done through the Supabase REST API, which
has no SQL execution path. Verify it manually: run 001, 002 and 003 again in the Supabase SQL
editor, confirm each completes without error, and confirm `check_schema.py` still exits 0 and
table row counts are unchanged.
"""

from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from hargaikan.config import SUPABASE_ANON_KEY, SUPABASE_SERVICE_ROLE_KEY, SUPABASE_URL, get_secret

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "check_schema.py"
INSUFFICIENT_PRIVILEGE = "42501"  # permission denied or RLS violation


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_schema_script", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def creds() -> dict[str, str]:
    env_file = ROOT / ".env"
    if env_file.is_file():
        from dotenv import load_dotenv

        load_dotenv(env_file, override=False)
    names = (SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, SUPABASE_ANON_KEY)
    values = {name: get_secret(name) for name in names}
    missing = [name for name, value in values.items() if not value]
    if missing:
        pytest.skip(f"Supabase credentials not configured: {', '.join(missing)}")
    return values  # type: ignore[return-value]


@pytest.fixture(scope="module")
def service_client(creds: dict[str, str]) -> Any:
    from supabase import create_client

    return create_client(creds[SUPABASE_URL], creds[SUPABASE_SERVICE_ROLE_KEY])


@pytest.fixture(scope="module")
def anon_client(creds: dict[str, str]) -> Any:
    from supabase import create_client

    return create_client(creds[SUPABASE_URL], creds[SUPABASE_ANON_KEY])


def _error_code(exc: BaseException) -> str | None:
    code = getattr(exc, "code", None)
    return str(code) if code is not None else None


def test_check_schema_reports_no_problems(service_client: Any) -> None:
    problems = _load_script().collect_problems(service_client)
    assert problems == [], "\n".join(p.describe() for p in problems)


def test_anon_cannot_insert_into_species(anon_client: Any, service_client: Any) -> None:
    from postgrest.exceptions import APIError

    species_id = str(uuid.uuid4())
    row = {"id": species_id, "code": f"PBT_RLS_{species_id[:8]}", "canonical_name_ms": "UJIAN RLS"}
    try:
        with pytest.raises(APIError) as excinfo:
            anon_client.table("species").insert(row).execute()
        # 42501 = denied by privileges/RLS. Any other code (e.g. table not found) is a real failure.
        assert _error_code(excinfo.value) == INSUFFICIENT_PRIVILEGE
    finally:
        service_client.table("species").delete().eq("id", species_id).execute()


@pytest.mark.parametrize("function, params", [("promote_batch", {"p_batch": str(uuid.uuid4())}),
                                              ("discard_batch", {"p_batch": str(uuid.uuid4())}),
                                              ("schema_report", None)])
def test_anon_cannot_call_service_functions(anon_client: Any, function: str, params: dict | None) -> None:
    from postgrest.exceptions import APIError

    with pytest.raises(APIError) as excinfo:
        anon_client.rpc(function, params or {}).execute()
    assert _error_code(excinfo.value) == INSUFFICIENT_PRIVILEGE


def test_anon_select_on_price_bands_returns_no_staged_rows(anon_client: Any) -> None:
    response = (
        anon_client.table("price_bands").select("id, publish_status").neq("publish_status", "live").limit(50).execute()
    )
    assert response.data == []
