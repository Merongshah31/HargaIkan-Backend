"""Unit tests for hargaikan.pipeline.schema_check and scripts/check_schema.py (no network)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
import supabase

from hargaikan.pipeline.schema_check import (
    EXPECTED_SCHEMA,
    Problem,
    compare_schema,
    exit_code,
    parse_schema_report,
)

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_schema.py"
FAKE_URL = "https://example-project.supabase.co"
FAKE_KEY = "fake-service-role-key-must-never-be-printed"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_schema_script", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _report(schema: dict[str, frozenset[str]], rls: bool = True) -> dict:
    return {"tables": {t: {"columns": sorted(cols), "rls": rls} for t, cols in schema.items()}}


def test_full_schema_has_no_problems() -> None:
    columns, rls = parse_schema_report(_report(EXPECTED_SCHEMA))
    assert compare_schema(EXPECTED_SCHEMA, columns, rls) == []
    assert exit_code([]) == 0


def test_problems_are_sorted_and_described() -> None:
    columns = {t: set(c) for t, c in EXPECTED_SCHEMA.items() if t != "species"}
    columns["locations"].discard("lat")
    rls = {t: t != "price_bands" for t in columns}
    problems = compare_schema(EXPECTED_SCHEMA, columns, rls)
    assert problems == [
        Problem("missing_table", "species"),
        Problem("missing_column", "locations", "lat"),
        Problem("rls_disabled", "price_bands"),
    ]
    assert [p.describe() for p in problems] == [
        "missing table: species",
        "missing column: locations.lat",
        "row level security disabled: price_bands",
    ]
    assert exit_code(problems) == 1


@pytest.mark.parametrize(
    "payload",
    [None, [], {}, {"tables": []}, {"tables": {"t": {"columns": "id", "rls": True}}},
     {"tables": {"t": {"columns": ["id"], "rls": "yes"}}}, {"tables": {"t": None}}],
)
def test_parse_schema_report_rejects_malformed_payloads(payload: object) -> None:
    with pytest.raises(ValueError):
        parse_schema_report(payload)


class _FakeClient:
    def __init__(self, data: object) -> None:
        self.data = data
        self.calls: list[str] = []

    def rpc(self, name: str) -> SimpleNamespace:
        self.calls.append(name)
        return SimpleNamespace(execute=lambda: SimpleNamespace(data=self.data))


def _patch_secrets(monkeypatch: pytest.MonkeyPatch, module: ModuleType, values: dict[str, str]) -> None:
    monkeypatch.setattr(module, "load_dotenv_if_present", lambda *a, **k: None)
    monkeypatch.setattr(module, "get_secret", lambda name: values.get(name))


@pytest.mark.parametrize(
    "present, missing",
    [({}, ["SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY"]),
     ({"SUPABASE_URL": FAKE_URL}, ["SUPABASE_SERVICE_ROLE_KEY"]),
     ({"SUPABASE_SERVICE_ROLE_KEY": FAKE_KEY}, ["SUPABASE_URL"])],
)
def test_script_exits_2_naming_missing_keys(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], present: dict[str, str], missing: list[str]
) -> None:
    module = _load_script()
    _patch_secrets(monkeypatch, module, present)
    assert module.main([]) == 2
    out = capsys.readouterr()
    for name in missing:
        assert name in out.err
    assert FAKE_KEY not in out.out + out.err
    assert FAKE_URL not in out.out + out.err


@pytest.mark.parametrize("drop_table, expected_exit", [(None, 0), ("travel_estimates", 1)])
def test_script_reports_problems_and_exit_status(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], drop_table: str | None, expected_exit: int
) -> None:
    module = _load_script()
    _patch_secrets(monkeypatch, module, {"SUPABASE_URL": FAKE_URL, "SUPABASE_SERVICE_ROLE_KEY": FAKE_KEY})
    schema = {t: c for t, c in EXPECTED_SCHEMA.items() if t != drop_table}
    client = _FakeClient(_report(schema))
    seen: list[tuple[str, str]] = []

    def fake_create_client(url: str, key: str) -> _FakeClient:
        seen.append((url, key))
        return client

    monkeypatch.setattr(supabase, "create_client", fake_create_client)
    assert module.main([]) == expected_exit
    out = capsys.readouterr()
    assert seen == [(FAKE_URL, FAKE_KEY)]
    assert client.calls == ["schema_report"]
    if drop_table:
        assert f"missing table: {drop_table}" in out.out
    else:
        assert "OK" in out.out
    assert FAKE_KEY not in out.out + out.err


def test_script_rpc_failure_exits_1_without_leaking_secrets(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _load_script()
    _patch_secrets(monkeypatch, module, {"SUPABASE_URL": FAKE_URL, "SUPABASE_SERVICE_ROLE_KEY": FAKE_KEY})

    def failing_create_client(url: str, key: str) -> None:
        raise RuntimeError(f"cannot connect to {url} with {key}")

    monkeypatch.setattr(supabase, "create_client", failing_create_client)
    assert module.main([]) == 1
    out = capsys.readouterr()
    assert "001_catalog_prices.sql" in out.err
    assert FAKE_KEY not in out.out + out.err
    assert FAKE_URL not in out.out + out.err
