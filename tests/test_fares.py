"""Transit fare table loader and lookup (hargaikan.data.fares)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from hargaikan.data.fares import FareEntry, FareTableError, load_fare_table, lookup_fare

ROOT = Path(__file__).resolve().parents[1]

# Test fixtures only: made-up station names and fares, never shipped.
VALID = """
- mode: mrt
  from_station: Stesen A
  to_station: Stesen B
  fare_rm: 2.5
  source: test fixture
  updated_on: 2025-01-10
- mode: bas
  from_location_code: LOC_1
  to_location_code: LOC_2
  fare_rm: 1
  source: test fixture
  updated_on: "2025-02-01"
- mode: mrt
  from_station: stesen b
  to_station: STESEN  A
  fare_rm: 2.7
  source: test fixture (newer)
  updated_on: 2025-03-01
"""


def test_shipped_fare_table_is_empty() -> None:
    assert load_fare_table((ROOT / "data" / "curated" / "transit_fares.yaml").read_text(encoding="utf-8")) == []


@pytest.mark.parametrize("text", ["", "[]", "null", "# only a comment\n"])
def test_empty_tables(text: str) -> None:
    assert load_fare_table(text) == []


def test_valid_entries_are_parsed() -> None:
    entries = load_fare_table(VALID)
    assert entries[0] == FareEntry("mrt", "Stesen A", "Stesen B", "station", 2.5, "test fixture", date(2025, 1, 10))
    assert entries[1].endpoint_kind == "location_code" and entries[1].fare_rm == 1.0
    assert entries[1].updated_on == date(2025, 2, 1)


def test_lookup_is_symmetric_and_prefers_latest() -> None:
    entries = load_fare_table(VALID)
    forward = lookup_fare(entries, "mrt", "Stesen A", "Stesen B")
    backward = lookup_fare(entries, "MRT", "stesen b", "stesen a")
    assert forward is not None and forward == backward and forward.fare_rm == 2.7
    assert lookup_fare(entries, "bas", "LOC_2", "LOC_1") == entries[1]


@pytest.mark.parametrize(
    ("mode", "origin", "destination"),
    [("lrt", "Stesen A", "Stesen B"), ("mrt", "Stesen A", "Stesen C"), ("mrt", "", "Stesen B"), ("bas", "LOC_1", "LOC_1")],
)
def test_lookup_misses_return_none(mode: str, origin: str, destination: str) -> None:
    assert lookup_fare(load_fare_table(VALID), mode, origin, destination) is None


BASE = {"mode": "mrt", "from_station": "A", "to_station": "B", "fare_rm": 2, "source": "s", "updated_on": "2025-01-01"}


def _yaml(entry: dict) -> str:
    import yaml

    return yaml.safe_dump([entry])


@pytest.mark.parametrize(
    ("change", "needle"),
    [
        ({"fare_rm": 0}, "fare_rm"),
        ({"fare_rm": -1.5}, "fare_rm"),
        ({"fare_rm": True}, "fare_rm"),
        ({"fare_rm": "2"}, "fare_rm"),
        ({"fare_rm": float("nan")}, "fare_rm"),
        ({"source": ""}, "source"),
        ({"source": None}, "source"),
        ({"updated_on": None}, "updated_on"),
        ({"updated_on": "10/01/2025"}, "updated_on"),
        ({"mode": "teksi"}, "mode"),
        ({"from_location_code": "X", "to_location_code": "Y"}, "exactly one"),
        ({"to_station": None}, "non-empty"),
        ({"to_station": " a "}, "same"),
        ({"extra": 1}, "unknown field"),
    ],
)
def test_invalid_entries_are_rejected(change: dict, needle: str) -> None:
    with pytest.raises(FareTableError) as exc:
        load_fare_table(_yaml({**BASE, **change}))
    assert any(needle in p for p in exc.value.problems)


def test_missing_source_and_date_are_rejected() -> None:
    entry = {k: v for k, v in BASE.items() if k not in ("source", "updated_on")}
    with pytest.raises(FareTableError) as exc:
        load_fare_table(_yaml(entry))
    assert len(exc.value.problems) == 2


@pytest.mark.parametrize("text", ["{mode: mrt}", "- just a string", "[: bad"])
def test_bad_shapes_are_rejected(text: str) -> None:
    with pytest.raises(FareTableError):
        load_fare_table(text)
