"""Maintained public transport fare table (``data/curated/transit_fares.yaml``).

Pure logic: parses YAML *text* only; reading the file is the caller's job. No supabase,
pinecone, requests, streamlit or fastapi imports.

Fares are never invented or estimated: a route that has no entry gives ``None`` from
:func:`lookup_fare`, and the travel calculator then needs the fare from the user. Every entry
must name its ``source`` and the date it was checked (``updated_on``).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal, get_args

import yaml

TransitMode = Literal["mrt", "lrt", "monorel", "bas"]
EndpointKind = Literal["location_code", "station"]

_FIELDS = frozenset(
    {"mode", "from_location_code", "to_location_code", "from_station", "to_station", "fare_rm", "source", "updated_on"}
)
_PAIRS: dict[EndpointKind, tuple[str, str]] = {
    "location_code": ("from_location_code", "to_location_code"),
    "station": ("from_station", "to_station"),
}


@dataclass(frozen=True)
class FareEntry:
    mode: TransitMode
    origin: str
    destination: str
    endpoint_kind: EndpointKind  # whether origin/destination are location codes or station names
    fare_rm: float  # one-way fare, > 0
    source: str
    updated_on: date


class FareTableError(ValueError):
    """The fare table text is invalid. ``problems`` lists every problem found."""

    def __init__(self, problems: Sequence[str]) -> None:
        self.problems = tuple(problems)
        super().__init__("transit fare table rejected:\n" + "\n".join(f"- {p}" for p in self.problems))


def _text(v: Any) -> str | None:
    return v.strip() if isinstance(v, str) and v.strip() else None


def _date(v: Any) -> date | None:
    if isinstance(v, date):  # PyYAML parses unquoted YYYY-MM-DD to datetime.date
        return v
    if isinstance(v, str):
        try:
            return date.fromisoformat(v.strip())
        except ValueError:
            return None
    return None


def _key(text: str) -> str:
    return " ".join(text.split()).casefold()


def load_fare_table(text: str) -> list[FareEntry]:
    """Parse and validate fare table YAML. Empty text, ``null`` and ``[]`` give [].

    Each entry needs ``mode`` (mrt|lrt|monorel|bas), exactly one endpoint pair
    (from/to_location_code or from/to_station, different from each other), ``fare_rm`` > 0,
    a non-empty ``source`` and an ISO ``updated_on`` date. Unknown fields are rejected. Raises
    :class:`FareTableError` listing every problem; nothing is returned if any entry is invalid.
    """
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise FareTableError([f"file: invalid YAML ({type(exc).__name__})"]) from None
    if data is None:
        return []
    if not isinstance(data, list):
        raise FareTableError([f"file: top level must be a list, got {type(data).__name__}"])

    problems: list[str] = []
    out: list[FareEntry] = []
    for i, e in enumerate(data):
        where = f"entry {i}"
        if not isinstance(e, dict):
            problems.append(f"{where}: must be a mapping")
            continue
        start = len(problems)
        problems += [f"{where}: unknown field {k!r}" for k in e if k not in _FIELDS]
        mode = e.get("mode")
        if mode not in get_args(TransitMode):
            problems.append(f"{where}: mode must be one of {', '.join(get_args(TransitMode))}")
        present = [kind for kind, (a, b) in _PAIRS.items() if a in e or b in e]
        endpoints: tuple[EndpointKind, str, str] | None = None
        if len(present) != 1:
            problems.append(f"{where}: give exactly one of from/to_location_code or from/to_station")
        else:
            kind = present[0]
            a, b = _PAIRS[kind]
            origin, destination = _text(e.get(a)), _text(e.get(b))
            if origin is None or destination is None:
                problems.append(f"{where}: {a} and {b} must both be non-empty text")
            elif _key(origin) == _key(destination):
                problems.append(f"{where}: origin and destination are the same")
            else:
                endpoints = (kind, origin, destination)
        fare = e.get("fare_rm")
        if isinstance(fare, bool) or not isinstance(fare, (int, float)) or not math.isfinite(fare) or fare <= 0:
            problems.append(f"{where}: fare_rm must be a number greater than 0")
        source = _text(e.get("source"))
        if source is None:
            problems.append(f"{where}: source is required")
        updated_on = _date(e.get("updated_on"))
        if updated_on is None:
            problems.append(f"{where}: updated_on is required (YYYY-MM-DD)")
        if len(problems) == start and endpoints is not None:
            kind, origin, destination = endpoints
            out.append(FareEntry(mode, origin, destination, kind, float(fare), source, updated_on))  # type: ignore[arg-type]
    if problems:
        raise FareTableError(problems)
    return out


def lookup_fare(entries: Sequence[FareEntry], mode: str, origin: str, destination: str) -> FareEntry | None:
    """The entry for ``mode`` between ``origin`` and ``destination`` in either direction, or None.

    Matching ignores case and repeated whitespace. If several entries match, the most recently
    updated one wins (ties: first in file order).
    """
    if not origin or not destination:
        return None
    m, o, d = mode.strip().casefold(), _key(origin), _key(destination)
    best: FareEntry | None = None
    for e in entries:
        if e.mode != m:
            continue
        eo, ed = _key(e.origin), _key(e.destination)
        if (eo, ed) in ((o, d), (d, o)):
            if best is None or e.updated_on > best.updated_on:
                best = e
    return best
