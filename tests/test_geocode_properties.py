"""Property-based tests for hargaikan.pipeline.geocode (Properties 23 and 24 of the design).

All HTTP is faked and time comes from a fake clock; no test sends a real request.
"""

from __future__ import annotations

import math
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

from hargaikan.config import Settings
from hargaikan.pipeline.cleaning import ParsedPremise
from hargaikan.pipeline.geocode import (
    GeocodeEntry,
    RateLimiter,
    build_query,
    geocode_premises,
    interpret_response,
    load_cache,
    save_cache,
)

SETTINGS = Settings()
BBOX = SETTINGS.kl_bbox
LAT_MIN, LNG_MIN, LAT_MAX, LNG_MAX = BBOX
# Float tolerance for start-time differences computed from a float fake clock.
EPS = 1e-9


# --- Fakes ---------------------------------------------------------------------------------------


class FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.t = start
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        assert seconds >= 0
        self.sleeps.append(seconds)
        self.t += seconds


class FakeHttp:
    """Records every call with its start time and returns a canned response per query."""

    def __init__(self, clock: FakeClock, responses: dict[str, list] | None = None, latencies=()) -> None:
        self.clock = clock
        self.responses = responses or {}
        self.latencies = list(latencies) or [0.0]
        self.calls: list[dict] = []

    def __call__(self, url: str, params: dict, headers: dict, timeout: float) -> list[dict]:
        self.calls.append({"t": self.clock.t, "url": url, "params": params, "headers": headers, "timeout": timeout})
        self.clock.t += self.latencies[(len(self.calls) - 1) % len(self.latencies)]
        return self.responses.get(params["q"], [])


def _fixed_now() -> datetime:
    return datetime(2026, 1, 5, 8, 30, tzinfo=timezone.utc)


# --- Shared strategies ---------------------------------------------------------------------------

csv_text = st.characters(blacklist_categories=("Cs",), blacklist_characters="\x00")
key_text = st.text(alphabet=csv_text, min_size=1, max_size=12).filter(lambda s: s.strip() == s and s != "")
branch_text = st.one_of(st.none(), st.text(alphabet=csv_text, min_size=1, max_size=8).filter(lambda s: s.strip() == s and s != ""))
contacts = st.emails() | st.text(alphabet=csv_text, min_size=1, max_size=20).filter(lambda s: s.strip() == s and s != "")
timestamps = st.datetimes(
    min_value=datetime(2000, 1, 1), max_value=datetime(2100, 1, 1), timezones=st.sampled_from([timezone.utc, None])
)
finite = st.floats(allow_nan=False, allow_infinity=False)


@st.composite
def premise_for_key(draw, key: str) -> ParsedPremise:
    chain = draw(key_text)
    branch = draw(branch_text)
    return ParsedPremise(key=key, chain=chain, branch=branch, display=key)


@st.composite
def cache_entry(draw, key: str) -> GeocodeEntry:
    status = draw(st.sampled_from(["ok", "not_found", "out_of_area", "manual"]))
    has = status in ("ok", "manual")
    return GeocodeEntry(
        premise_key=key,
        query=draw(st.text(alphabet=csv_text, max_size=20)),
        lat=draw(finite) if has else None,
        lng=draw(finite) if has else None,
        display_name=draw(st.none() | st.text(alphabet=csv_text, min_size=1, max_size=20)),
        status=status,
        fetched_at=draw(timestamps),
    )


@st.composite
def premises_and_cache(draw):
    keys = draw(st.lists(key_text, min_size=1, max_size=8, unique=True))
    by_key = {k: draw(premise_for_key(k)) for k in keys}
    # Premises may repeat (the same branch in several snapshots).
    premises = draw(st.lists(st.sampled_from(keys), max_size=12).map(lambda ks: [by_key[k] for k in ks]))
    cached_keys = draw(st.lists(st.sampled_from(keys), unique=True))
    # The cache may also hold premises not in this run.
    extra = draw(st.lists(key_text.filter(lambda k: k not in by_key), max_size=3, unique=True))
    cache = {k: draw(cache_entry(k)) for k in [*cached_keys, *extra]}
    return premises, cache


# --- Property 23 ---------------------------------------------------------------------------------


# Feature: hargaikan-v2, Property 23: Geocoder is polite and cache-first
@settings(max_examples=150, deadline=None)
@given(
    data=premises_and_cache(),
    contact=contacts,
    latencies=st.lists(st.sampled_from([0.0, 0.001, 0.4, 0.999, 1.0, 2.5]), min_size=1, max_size=5),
    start=st.floats(min_value=0, max_value=1e6),
)
def test_geocoder_is_polite_and_cache_first(data, contact, latencies, start) -> None:
    """No request for a cached premise, every request carries the HargaIkan User-Agent with the
    contact, request starts are ≥ 1 s apart, every premise ends up cached, and the cache survives
    a save/load round trip.

    **Validates: Requirements 9.1, 9.3, 9.4, 9.5**
    """
    premises, cache = data
    original = dict(cache)
    clock = FakeClock(start)
    http = FakeHttp(clock, latencies=latencies)
    limiter = RateLimiter(clock=clock.clock, sleep=clock.sleep)

    result = geocode_premises(premises, cache, http, limiter, contact, SETTINGS, now=_fixed_now)

    # 9.4: cached premises are never requested; each uncached premise is requested exactly once.
    uncached = {p.key: p for p in premises if p.key not in original}
    requested = [c["params"]["q"] for c in http.calls]
    assert sorted(requested) == sorted(build_query(p) for p in uncached.values())
    assert cache == original  # input cache not mutated
    for key, entry in original.items():
        assert result[key] is entry

    # 9.1: User-Agent identifies HargaIkan and the contact address on every request.
    for call in http.calls:
        ua = call["headers"]["User-Agent"]
        assert "HargaIkan" in ua and contact in ua

    # 9.3: at least 1 s between consecutive request starts.
    starts = [c["t"] for c in http.calls]
    for a, b in zip(starts, starts[1:]):
        assert b - a >= 1.0 - EPS

    # 9.5: every premise has an entry afterwards, including not-found ones.
    for p in premises:
        assert p.key in result
    for key in uncached:
        assert result[key].status == "not_found"  # fake returns [] for every query

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "geocode_cache.csv"
        save_cache(path, result)
        assert load_cache(path) == result


# --- Property 24 ---------------------------------------------------------------------------------


def _inside(lat: float, lng: float) -> bool:
    return LAT_MIN <= lat <= LAT_MAX and LNG_MIN <= lng <= LNG_MAX


inside_point = st.tuples(
    st.floats(min_value=LAT_MIN, max_value=LAT_MAX) | st.sampled_from([LAT_MIN, LAT_MAX]),
    st.floats(min_value=LNG_MIN, max_value=LNG_MAX) | st.sampled_from([LNG_MIN, LNG_MAX]),
)
outside_point = st.tuples(st.floats(min_value=-90, max_value=90), st.floats(min_value=-180, max_value=180)).filter(
    lambda p: not _inside(*p)
) | st.sampled_from([(LAT_MIN - 1e-9, LNG_MIN), (LAT_MAX, LNG_MAX + 1e-9), (0.0, 0.0), (3.14, 103.0)])


def _render(value: float, as_str: bool):
    return repr(value) if as_str else value


@st.composite
def response_item(draw):
    """(item, valid_point_or_None). Valid items carry a finite lat/lon pair."""
    kind = draw(st.sampled_from(["inside", "outside", "garbage"]))
    name = draw(st.none() | st.text(max_size=15))
    if kind == "garbage":
        item = draw(
            st.sampled_from(
                [
                    {"lat": "abc", "lon": "101.7"},
                    {"lat": "3.1", "lon": None},
                    {"lon": "101.7"},
                    {"lat": "nan", "lon": "101.7"},
                    {"lat": "3.1", "lon": "inf"},
                    {"lat": True, "lon": "101.7"},
                    {},
                    "not a dict",
                    5,
                ]
            )
        )
        return item, None
    lat, lng = draw(inside_point if kind == "inside" else outside_point)
    as_str = draw(st.booleans())
    item = {"lat": _render(lat, as_str), "lon": _render(lng, as_str)}
    if name is not None:
        item["display_name"] = name
    return item, (lat, lng)


responses = st.lists(response_item(), max_size=6)


def _check_against_response(lat, lng, status, pairs) -> None:
    valid = [pt for _, pt in pairs if pt is not None]
    inside = [pt for pt in valid if _inside(*pt)]
    if inside:
        assert status == "ok"
        assert (lat, lng) == inside[0]
        assert _inside(lat, lng)
    else:
        assert lat is None and lng is None
        assert status == ("out_of_area" if valid else "not_found")


# Feature: hargaikan-v2, Property 24: Geocoded coordinates are never invented
@settings(max_examples=200, deadline=None)
@given(pairs=responses)
def test_interpret_response_never_invents_coordinates(pairs) -> None:
    """A non-null coordinate is exactly the first response coordinate inside the bounding box;
    otherwise lat and lng are null with status not_found or out_of_area.

    **Validates: Requirements 9.6, 9.7**
    """
    lat, lng, _name, status = interpret_response([item for item, _ in pairs], BBOX)
    _check_against_response(lat, lng, status, pairs)
    assert all(not (isinstance(v, float) and math.isnan(v)) for v in (lat, lng))


# Feature: hargaikan-v2, Property 24: Geocoded coordinates are never invented
@settings(max_examples=150, deadline=None)
@given(data=st.data(), keys=st.lists(key_text, min_size=1, max_size=6, unique=True), retry=st.booleans())
def test_geocoded_entries_come_from_response_or_manual(data, keys, retry) -> None:
    """Every stored coordinate comes from an in-bbox response coordinate or a manual cache entry;
    manual entries are used as-is and never re-requested.

    **Validates: Requirements 9.6, 9.7**
    """
    # chain = unique key and no branch, so each premise has its own query.
    premises = [ParsedPremise(key=k, chain=k, branch=None, display=k) for k in keys]
    cached_keys = data.draw(st.lists(st.sampled_from(keys), unique=True))
    cache = {k: data.draw(cache_entry(k)) for k in cached_keys}
    per_query = {build_query(p): data.draw(responses) for p in premises}
    clock = FakeClock()
    http = FakeHttp(clock, responses={q: [item for item, _ in pairs] for q, pairs in per_query.items()})
    limiter = RateLimiter(clock=clock.clock, sleep=clock.sleep)

    result = geocode_premises(premises, cache, http, limiter, "ops@example.com", SETTINGS, retry_not_found=retry, now=_fixed_now)

    requested = [c["params"]["q"] for c in http.calls]
    assert len(requested) == len(set(requested))
    for p in premises:
        entry = result[p.key]
        before = cache.get(p.key)
        query = build_query(p)
        if before is not None and before.status in ("manual", "ok"):
            assert entry is before  # manual and ok entries are used as-is
            assert query not in requested
        elif query in requested:
            assert before is None or (retry and before.status in ("not_found", "out_of_area"))
            _check_against_response(entry.lat, entry.lng, entry.status, per_query[query])
        else:
            assert entry is before and not retry  # served from cache
        if entry.lat is not None:
            assert entry.lng is not None and entry.status in ("ok", "manual")
        else:
            assert entry.lng is None and entry.status in ("not_found", "out_of_area")


# --- Fake clock sanity: the limiter itself -------------------------------------------------------


# Feature: hargaikan-v2, Property 23: Geocoder is polite and cache-first
@settings(max_examples=100, deadline=None)
@given(
    gaps=st.lists(st.floats(min_value=0, max_value=3), min_size=1, max_size=10),
    interval=st.floats(min_value=1.0, max_value=5.0),
)
def test_rate_limiter_spaces_wait_returns(gaps, interval) -> None:
    """Consecutive returns from RateLimiter.wait are at least min_interval_s apart.

    **Validates: Requirements 9.3**
    """
    clock = FakeClock()
    limiter = RateLimiter(min_interval_s=interval, clock=clock.clock, sleep=clock.sleep)
    times = []
    for gap in gaps:
        clock.t += gap
        limiter.wait()
        times.append(clock.t)
    for a, b in zip(times, times[1:]):
        assert b - a >= interval - EPS
    if len(gaps) == 1:
        assert clock.sleeps == []  # the first call never waits
