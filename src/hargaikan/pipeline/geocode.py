"""Premise geocoding through OpenStreetMap Nominatim, cache first (Requirement 9).

Offline only: this module is called from ``scripts/ingest.py`` and ``01_ingest.ipynb`` and must
never be imported by ``app.py``, ``pages/``, ``ui/`` or ``services/``.

The decision logic (:func:`build_query`, :func:`interpret_response`, :func:`geocode_premises`)
is pure: HTTP goes through an injected :class:`HttpGet` and time through an injected
:class:`RateLimiter`, so tests never touch the network. :func:`requests_http_get` is the real
HTTP implementation and :func:`load_cache` / :func:`save_cache` handle ``data/geocode_cache.csv``.

Coordinates are never estimated or invented. They come only from a Nominatim result inside the
configured Kuala Lumpur bounding box (status ``ok``) or from a cache row a maintainer wrote with
status ``manual``. Everything else is stored with null lat/lng and status ``not_found`` or
``out_of_area``.
"""

from __future__ import annotations

import csv
import logging
import math
import os
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Protocol, get_args

from hargaikan.config import NOMINATIM_CONTACT, Settings
from hargaikan.pipeline.cleaning import ParsedPremise

logger = logging.getLogger(__name__)

GeocodeStatus = Literal["ok", "not_found", "out_of_area", "manual"]
GEOCODE_STATUSES: tuple[str, ...] = get_args(GeocodeStatus)
# Statuses whose entries carry coordinates; the others always have null lat/lng.
STATUSES_WITH_COORDS = frozenset({"ok", "manual"})
# Statuses a re-run with retry_not_found may look up again. ok and manual are never retried.
RETRYABLE_STATUSES = frozenset({"not_found", "out_of_area"})

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT_PRODUCT = "HargaIkan/2.0"
MIN_REQUEST_INTERVAL_S = 1.0  # Nominatim usage policy: at most one request per second
CACHE_COLUMNS: tuple[str, ...] = ("premise_key", "query", "lat", "lng", "display_name", "status", "fetched_at")
DEFAULT_CACHE_PATH = Path("data") / "geocode_cache.csv"

BBox = tuple[float, float, float, float]  # lat_min, lng_min, lat_max, lng_max (Settings.kl_bbox)


class MissingContactError(RuntimeError):
    """Raised before any request when no Nominatim contact address is configured."""

    def __init__(self) -> None:
        super().__init__(
            f"{NOMINATIM_CONTACT} is not set. Nominatim requires a contact address in the "
            f"User-Agent; set {NOMINATIM_CONTACT} in .env or st.secrets before geocoding."
        )


@dataclass(frozen=True)
class GeocodeEntry:
    premise_key: str
    query: str
    lat: float | None
    lng: float | None
    display_name: str | None
    status: GeocodeStatus
    fetched_at: datetime


class HttpGet(Protocol):
    def __call__(self, url: str, params: dict, headers: dict, timeout: float) -> list[dict]: ...


class RateLimiter:
    """Spaces calls to :meth:`wait` at least ``min_interval_s`` apart (measured start to start).

    ``clock`` and ``sleep`` are injectable so tests can use a fake clock. The interval cannot be
    set below one second, the Nominatim usage policy limit.
    """

    def __init__(
        self,
        min_interval_s: float = MIN_REQUEST_INTERVAL_S,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not min_interval_s >= MIN_REQUEST_INTERVAL_S:  # also rejects NaN
            raise ValueError(f"min_interval_s must be at least {MIN_REQUEST_INTERVAL_S} s")
        self.min_interval_s = float(min_interval_s)
        self._clock = clock
        self._sleep = sleep
        self._last: float | None = None

    def wait(self) -> None:
        """Block until at least ``min_interval_s`` has passed since the previous call returned."""
        if self._last is not None:
            target = self._last + self.min_interval_s
            # Loop: a real or fake sleep may return slightly early.
            while (now := self._clock()) < target:
                self._sleep(target - now)
        self._last = self._clock()


# --- Pure helpers ------------------------------------------------------------------------------


def _clean_contact(contact: str | None) -> str | None:
    if contact is None:
        return None
    text = str(contact).strip()
    return text or None


def user_agent(contact: str) -> str:
    """User-Agent identifying HargaIkan and the contact address, e.g. "HargaIkan/2.0 (+ops@example.com)"."""
    cleaned = _clean_contact(contact)
    if cleaned is None:
        raise MissingContactError()
    return f"{USER_AGENT_PRODUCT} (+{cleaned})"


def build_query(p: ParsedPremise) -> str:
    """Free-text Nominatim query for a premise: "AEON AU2, Kuala Lumpur, Malaysia"."""
    name = " ".join(part for part in (p.chain, p.branch) if part)
    return f"{name}, Kuala Lumpur, Malaysia"


def nominatim_params(query: str) -> dict:
    """Query parameters for one Nominatim search."""
    return {"q": query, "format": "jsonv2", "limit": 5, "countrycodes": "my"}


def _coord(value: object) -> float | None:
    """Nominatim returns coordinates as strings. Unparseable or non-finite values → None."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def in_bbox(lat: float, lng: float, bbox: BBox) -> bool:
    lat_min, lng_min, lat_max, lng_max = bbox
    return lat_min <= lat <= lat_max and lng_min <= lng <= lng_max


def interpret_response(
    resp: list[dict], bbox: BBox
) -> tuple[float | None, float | None, str | None, str]:
    """Return (lat, lng, display_name, status) for a Nominatim result list.

    The first result inside ``bbox`` gives status ``ok`` with its own coordinates. Results that
    all lie outside give ``out_of_area``; no usable result gives ``not_found``. Non-ok statuses
    always have null coordinates. Nothing is averaged, rounded or estimated.
    """
    first_outside: str | None = None
    saw_outside = False
    for item in resp or ():
        if not isinstance(item, Mapping):
            continue
        lat, lng = _coord(item.get("lat")), _coord(item.get("lon"))
        if lat is None or lng is None:
            continue
        name = item.get("display_name")
        name = str(name) if name not in (None, "") else None
        if in_bbox(lat, lng, bbox):
            return lat, lng, name, "ok"
        if not saw_outside:
            saw_outside, first_outside = True, name
    if saw_outside:
        return None, None, first_outside, "out_of_area"
    return None, None, None, "not_found"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def geocode_premises(
    premises: Sequence[ParsedPremise],
    cache: dict[str, GeocodeEntry],
    http_get: HttpGet,
    limiter: RateLimiter,
    contact: str | None,
    s: Settings,
    *,
    retry_not_found: bool = False,
    now: Callable[[], datetime] = _utc_now,
) -> dict[str, GeocodeEntry]:
    """Geocode every premise, cache first. Returns a new cache including every result.

    * ``contact`` (falling back to ``s.nominatim_contact``) is required; without it
      :class:`MissingContactError` is raised before any request.
    * A premise already in ``cache`` is not requested again. With ``retry_not_found`` entries
      with status not_found or out_of_area are looked up again; ok and manual never are.
    * Every request waits on ``limiter`` first and carries the HargaIkan User-Agent.
    * Every completed lookup is recorded, including not_found and out_of_area. A failed request
      (network error, bad payload) is recorded as not_found with null coordinates.
    """
    contact = _clean_contact(contact) or _clean_contact(s.nominatim_contact)
    if contact is None:
        raise MissingContactError()
    headers = {"User-Agent": user_agent(contact)}

    result: dict[str, GeocodeEntry] = dict(cache)
    for premise in premises:
        cached = result.get(premise.key)
        if cached is not None and not (retry_not_found and cached.status in RETRYABLE_STATUSES):
            continue
        query = build_query(premise)
        limiter.wait()
        try:
            resp = http_get(NOMINATIM_URL, params=nominatim_params(query), headers=headers, timeout=s.service_timeout_s)
            if not isinstance(resp, list):
                raise ValueError("Nominatim response is not a list")
            lat, lng, display_name, status = interpret_response(resp, s.kl_bbox)
        except Exception as exc:  # recorded as not_found; never guess a coordinate
            logger.warning("Geocoding failed for %s (%s); recorded as not_found", premise.key, type(exc).__name__)
            lat, lng, display_name, status = None, None, None, "not_found"
        result[premise.key] = GeocodeEntry(
            premise_key=premise.key,
            query=query,
            lat=lat,
            lng=lng,
            display_name=display_name,
            status=status,  # type: ignore[arg-type]
            fetched_at=now(),
        )
    return result


# --- I/O: HTTP and the cache file --------------------------------------------------------------


def requests_http_get(url: str, params: dict, headers: dict, timeout: float) -> list[dict]:
    """Real :class:`HttpGet` using ``requests``. Raises on HTTP errors or a non-list payload."""
    import requests  # lazy: only the offline geocoding run needs it

    response = requests.get(url, params=params, headers=headers, timeout=timeout)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list):
        raise ValueError("Nominatim response is not a list")
    return payload


def _format_float(value: float | None) -> str:
    return "" if value is None else repr(float(value))  # repr round-trips exactly


def _parse_float(text: str, column: str, line: int) -> float | None:
    text = text.strip()
    if not text:
        return None
    value = _coord(text)
    if value is None:
        raise ValueError(f"geocode cache line {line}: {column} is not a finite number")
    return value


def load_cache(path: Path) -> dict[str, GeocodeEntry]:
    """Read ``data/geocode_cache.csv``. A missing file gives an empty cache.

    Raises ValueError (with the line number) for an unknown status, a missing or unparseable
    timestamp, or coordinates that do not match the status (ok/manual need both lat and lng,
    not_found/out_of_area need neither). An empty display_name loads as None.
    """
    path = Path(path)
    if not path.is_file():
        return {}
    entries: dict[str, GeocodeEntry] = {}
    with path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        missing = [c for c in CACHE_COLUMNS if c not in (reader.fieldnames or ())]
        if missing:
            raise ValueError(f"geocode cache is missing columns: {', '.join(missing)}")
        for row in reader:
            line = reader.line_num
            key = (row["premise_key"] or "").strip()
            if not key:
                raise ValueError(f"geocode cache line {line}: premise_key is empty")
            status = (row["status"] or "").strip()
            if status not in GEOCODE_STATUSES:
                raise ValueError(f"geocode cache line {line}: unknown status {status!r}")
            lat = _parse_float(row["lat"] or "", "lat", line)
            lng = _parse_float(row["lng"] or "", "lng", line)
            has_coords = lat is not None and lng is not None
            if (lat is None) != (lng is None):
                raise ValueError(f"geocode cache line {line}: lat and lng must both be set or both be empty")
            if status in STATUSES_WITH_COORDS and not has_coords:
                raise ValueError(f"geocode cache line {line}: status {status} needs lat and lng")
            if status not in STATUSES_WITH_COORDS and has_coords:
                raise ValueError(f"geocode cache line {line}: status {status} must have empty lat and lng")
            try:
                fetched_at = datetime.fromisoformat((row["fetched_at"] or "").strip())
            except ValueError:
                raise ValueError(f"geocode cache line {line}: fetched_at is not an ISO timestamp") from None
            entries[key] = GeocodeEntry(
                premise_key=key,
                query=row["query"] or "",
                lat=lat,
                lng=lng,
                display_name=row["display_name"] or None,
                status=status,  # type: ignore[arg-type]
                fetched_at=fetched_at,
            )
    return entries


def save_cache(path: Path, cache: Mapping[str, GeocodeEntry]) -> None:
    """Write the cache (sorted by premise_key) atomically, header row always included."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".geocode_cache.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            writer = csv.writer(fh, lineterminator="\n")
            writer.writerow(CACHE_COLUMNS)
            for key in sorted(cache):
                e = cache[key]
                writer.writerow(
                    (
                        e.premise_key,
                        e.query,
                        _format_float(e.lat),
                        _format_float(e.lng),
                        e.display_name or "",
                        e.status,
                        e.fetched_at.isoformat(),
                    )
                )
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
