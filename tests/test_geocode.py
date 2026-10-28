"""Unit and smoke tests for hargaikan.pipeline.geocode (Requirements 9.1–9.8).

No test sends a real request: HTTP is a fake or a monkeypatched ``requests.get``.
"""

from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path

import pytest

from hargaikan.config import NOMINATIM_CONTACT, Settings
from hargaikan.pipeline import geocode as geo
from hargaikan.pipeline.cleaning import normalise_premise
from hargaikan.pipeline.geocode import (
    CACHE_COLUMNS,
    NOMINATIM_URL,
    GeocodeEntry,
    MissingContactError,
    RateLimiter,
    build_query,
    geocode_premises,
    interpret_response,
    load_cache,
    requests_http_get,
    save_cache,
    user_agent,
)

ROOT = Path(__file__).resolve().parents[1]
SETTINGS = Settings()
BBOX = SETTINGS.kl_bbox
NOW = datetime(2026, 1, 5, 8, 30, tzinfo=timezone.utc)
AEON = normalise_premise("AEON ( AU2 )")
TESCO = normalise_premise("TESCO")


class FakeClock:
    def __init__(self) -> None:
        self.t = 0.0
        self.sleeps: list[float] = []
        self.reads = 0

    def clock(self) -> float:
        self.reads += 1
        return self.t

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.t += seconds


class RecordingHttp:
    def __init__(self, response=None, error: Exception | None = None) -> None:
        self.response = [] if response is None else response
        self.error = error
        self.calls: list[dict] = []

    def __call__(self, url, params, headers, timeout):
        self.calls.append({"url": url, "params": params, "headers": headers, "timeout": timeout})
        if self.error is not None:
            raise self.error
        return self.response


def _limiter(clock: FakeClock) -> RateLimiter:
    return RateLimiter(clock=clock.clock, sleep=clock.sleep)


# --- Pure helpers --------------------------------------------------------------------------------


def test_user_agent_identifies_hargaikan_and_contact() -> None:
    assert user_agent("ops@example.com") == "HargaIkan/2.0 (+ops@example.com)"


def test_build_query_examples() -> None:
    assert build_query(AEON) == "AEON AU2, Kuala Lumpur, Malaysia"
    assert build_query(TESCO) == "TESCO, Kuala Lumpur, Malaysia"


def test_interpret_response_examples() -> None:
    inside = {"lat": "3.1390", "lon": "101.6869", "display_name": "AEON AU2, KL"}
    outside = {"lat": "1.4927", "lon": "103.7414", "display_name": "Johor Bahru"}
    assert interpret_response([], BBOX) == (None, None, None, "not_found")
    assert interpret_response([outside], BBOX) == (None, None, "Johor Bahru", "out_of_area")
    assert interpret_response([outside, inside], BBOX) == (3.139, 101.6869, "AEON AU2, KL", "ok")
    assert interpret_response([{"lat": "x", "lon": "y"}], BBOX) == (None, None, None, "not_found")


def test_rate_limiter_rejects_interval_below_one_second() -> None:
    with pytest.raises(ValueError):
        RateLimiter(min_interval_s=0.5)
    with pytest.raises(ValueError):
        RateLimiter(min_interval_s=float("nan"))


def test_rate_limiter_waits_between_calls() -> None:
    clock = FakeClock()
    limiter = _limiter(clock)
    limiter.wait()
    assert clock.sleeps == []
    clock.t += 0.25
    limiter.wait()
    assert clock.sleeps == [pytest.approx(0.75)]


# --- Missing contact (9.2) -----------------------------------------------------------------------


@pytest.mark.parametrize("contact", [None, "", "   "])
@pytest.mark.parametrize("premises", [[AEON, TESCO], []])
def test_missing_contact_refuses_before_any_request(contact, premises) -> None:
    clock = FakeClock()
    http = RecordingHttp([{"lat": "3.1", "lon": "101.7"}])
    with pytest.raises(MissingContactError) as excinfo:
        geocode_premises(premises, {}, http, _limiter(clock), contact, Settings(nominatim_contact=None))
    assert NOMINATIM_CONTACT in str(excinfo.value)
    assert http.calls == []
    assert clock.reads == 0 and clock.sleeps == []


def test_user_agent_without_contact_names_the_setting() -> None:
    with pytest.raises(MissingContactError, match=NOMINATIM_CONTACT):
        user_agent("  ")


def test_contact_falls_back_to_settings() -> None:
    clock = FakeClock()
    http = RecordingHttp([])
    geocode_premises([AEON], {}, http, _limiter(clock), None, Settings(nominatim_contact="ops@example.com"), now=lambda: NOW)
    assert http.calls[0]["headers"]["User-Agent"] == "HargaIkan/2.0 (+ops@example.com)"


# --- geocode_premises behaviour ------------------------------------------------------------------


def test_request_shape_and_ok_entry() -> None:
    clock = FakeClock()
    http = RecordingHttp([{"lat": "3.1", "lon": "101.7", "display_name": "AEON AU2"}])
    result = geocode_premises([AEON], {}, http, _limiter(clock), "ops@example.com", SETTINGS, now=lambda: NOW)
    (call,) = http.calls
    assert call["url"] == NOMINATIM_URL
    assert call["params"] == {"q": "AEON AU2, Kuala Lumpur, Malaysia", "format": "jsonv2", "limit": 5, "countrycodes": "my"}
    assert call["timeout"] == SETTINGS.service_timeout_s
    assert result == {
        "AEON (AU2)": GeocodeEntry("AEON (AU2)", "AEON AU2, Kuala Lumpur, Malaysia", 3.1, 101.7, "AEON AU2", "ok", NOW)
    }


def test_network_error_is_recorded_as_not_found() -> None:
    clock = FakeClock()
    http = RecordingHttp(error=ConnectionError("boom"))
    result = geocode_premises([AEON], {}, http, _limiter(clock), "ops@example.com", SETTINGS, now=lambda: NOW)
    entry = result[AEON.key]
    assert (entry.lat, entry.lng, entry.status) == (None, None, "not_found")


def test_manual_entry_is_used_without_request_even_when_retrying() -> None:
    manual = GeocodeEntry(AEON.key, build_query(AEON), 3.11, 101.66, "verified by maintainer", "manual", NOW)
    not_found = GeocodeEntry(TESCO.key, build_query(TESCO), None, None, None, "not_found", NOW)
    cache = {AEON.key: manual, TESCO.key: not_found}
    clock = FakeClock()
    http = RecordingHttp([])

    result = geocode_premises([AEON, TESCO], cache, http, _limiter(clock), "ops@example.com", SETTINGS, now=lambda: NOW)
    assert http.calls == [] and result == cache

    result = geocode_premises(
        [AEON, TESCO], cache, http, _limiter(clock), "ops@example.com", SETTINGS, retry_not_found=True, now=lambda: NOW
    )
    assert [c["params"]["q"] for c in http.calls] == [build_query(TESCO)]
    assert result[AEON.key] is manual


# --- Cache file ----------------------------------------------------------------------------------


def test_committed_cache_is_header_only() -> None:
    path = ROOT / "data" / "geocode_cache.csv"
    assert path.read_text(encoding="utf-8").splitlines() == [",".join(CACHE_COLUMNS)]
    assert load_cache(path) == {}


def test_missing_cache_file_loads_empty(tmp_path: Path) -> None:
    assert load_cache(tmp_path / "absent.csv") == {}


def test_cache_round_trip_with_manual_rows(tmp_path: Path) -> None:
    cache = {
        "AEON (AU2)": GeocodeEntry("AEON (AU2)", "AEON AU2, Kuala Lumpur, Malaysia", 3.1234567890123, 101.7, "AEON, \"AU2\"", "ok", NOW),
        "TESCO": GeocodeEntry("TESCO", "TESCO, Kuala Lumpur, Malaysia", None, None, None, "not_found", NOW),
        "GIANT (X)": GeocodeEntry("GIANT (X)", "GIANT X, Kuala Lumpur, Malaysia", None, None, "Johor", "out_of_area", NOW),
        "PASAR": GeocodeEntry("PASAR", "PASAR, Kuala Lumpur, Malaysia", 3.2, 101.6, None, "manual", datetime(2026, 1, 1)),
    }
    path = tmp_path / "geocode_cache.csv"
    save_cache(path, cache)
    assert load_cache(path) == cache
    assert list(tmp_path.iterdir()) == [path]  # no temp files left behind


def test_hand_written_manual_row_is_loaded_and_used(tmp_path: Path) -> None:
    path = tmp_path / "geocode_cache.csv"
    path.write_text(
        ",".join(CACHE_COLUMNS) + "\n"
        "AEON (AU2),\"AEON AU2, Kuala Lumpur, Malaysia\",3.15,101.71,Checked on site,manual,2026-01-02T00:00:00+00:00\n",
        encoding="utf-8",
    )
    cache = load_cache(path)
    assert cache[AEON.key].status == "manual" and (cache[AEON.key].lat, cache[AEON.key].lng) == (3.15, 101.71)
    http = RecordingHttp([])
    result = geocode_premises([AEON], cache, http, _limiter(FakeClock()), "ops@example.com", SETTINGS)
    assert http.calls == [] and result[AEON.key] == cache[AEON.key]


@pytest.mark.parametrize(
    "row",
    [
        "K,q,3.1,101.7,,guessed,2026-01-01T00:00:00",  # unknown status
        "K,q,,,,manual,2026-01-01T00:00:00",  # manual without coordinates
        "K,q,3.1,101.7,,not_found,2026-01-01T00:00:00",  # not_found with coordinates
        "K,q,3.1,,,ok,2026-01-01T00:00:00",  # half a coordinate
        "K,q,abc,101.7,,ok,2026-01-01T00:00:00",  # unparseable
        "K,q,3.1,101.7,,ok,yesterday",  # bad timestamp
        ",q,3.1,101.7,,ok,2026-01-01T00:00:00",  # empty key
    ],
)
def test_invalid_cache_rows_are_rejected(tmp_path: Path, row: str) -> None:
    path = tmp_path / "geocode_cache.csv"
    path.write_text(",".join(CACHE_COLUMNS) + "\n" + row + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="line 2"):
        load_cache(path)


# --- requests_http_get (monkeypatched, no network) -----------------------------------------------


def test_requests_http_get_passes_arguments_through(monkeypatch: pytest.MonkeyPatch) -> None:
    import requests

    seen: dict = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            pass

        def json(self):
            return [{"lat": "3.1", "lon": "101.7"}]

    def fake_get(url, params, headers, timeout):
        seen.update(url=url, params=params, headers=headers, timeout=timeout)
        return FakeResponse()

    monkeypatch.setattr(requests, "get", fake_get)
    out = requests_http_get(NOMINATIM_URL, {"q": "x"}, {"User-Agent": "HargaIkan/2.0 (+a@b.c)"}, 10.0)
    assert out == [{"lat": "3.1", "lon": "101.7"}]
    assert seen == {"url": NOMINATIM_URL, "params": {"q": "x"}, "headers": {"User-Agent": "HargaIkan/2.0 (+a@b.c)"}, "timeout": 10.0}


def test_requests_http_get_rejects_non_list(monkeypatch: pytest.MonkeyPatch) -> None:
    import requests

    class FakeResponse:
        def raise_for_status(self) -> None:
            pass

        def json(self):
            return {"error": "x"}

    monkeypatch.setattr(requests, "get", lambda *a, **k: FakeResponse())
    with pytest.raises(ValueError):
        requests_http_get(NOMINATIM_URL, {}, {}, 1.0)


# --- Smoke: the app never imports the geocoder (9.8) ---------------------------------------------


def _app_files() -> list[Path]:
    files = [ROOT / "app.py"] if (ROOT / "app.py").is_file() else []
    for d in (ROOT / "pages", ROOT / "src" / "hargaikan" / "services", ROOT / "src" / "hargaikan" / "ui"):
        if d.is_dir():
            files.extend(p for p in d.rglob("*.py") if "__pycache__" not in p.parts)
    return sorted(files)


def _imports_geocode(tree: ast.AST) -> list[int]:
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(a.name == "hargaikan.pipeline.geocode" or a.name.startswith("hargaikan.pipeline.geocode.") for a in node.names):
                hits.append(node.lineno)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            names = {a.name for a in node.names}
            if module.endswith("pipeline.geocode") or module == "geocode":
                hits.append(node.lineno)
            elif (module.endswith("pipeline") or (node.level and module == "")) and "geocode" in names:
                hits.append(node.lineno)
        elif isinstance(node, ast.Constant) and node.value == "hargaikan.pipeline.geocode":
            hits.append(node.lineno)  # importlib.import_module("hargaikan.pipeline.geocode")
    return hits


def test_app_file_scan_is_not_vacuous() -> None:
    assert ROOT / "app.py" in _app_files()


@pytest.mark.parametrize("path", _app_files(), ids=lambda p: p.relative_to(ROOT).as_posix())
def test_app_pages_and_services_never_import_geocode(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    assert _imports_geocode(tree) == [], f"{path.relative_to(ROOT)} imports pipeline.geocode"


def test_import_detector_catches_known_forms() -> None:
    for src in (
        "import hargaikan.pipeline.geocode",
        "from hargaikan.pipeline.geocode import geocode_premises",
        "from hargaikan.pipeline import geocode",
        "from ..pipeline import geocode",
        "from ..pipeline.geocode import load_cache",
        "import importlib; importlib.import_module('hargaikan.pipeline.geocode')",
    ):
        assert _imports_geocode(ast.parse(src)), src
    assert _imports_geocode(ast.parse("from hargaikan.pipeline import cleaning")) == []


def test_module_exposes_designed_api() -> None:
    for name in ("GeocodeEntry", "HttpGet", "RateLimiter", "user_agent", "build_query", "interpret_response",
                 "geocode_premises", "load_cache", "save_cache", "requests_http_get", "MissingContactError"):
        assert hasattr(geo, name), name
