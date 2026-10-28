"""Weekly median series helpers (hargaikan.data.weekly)."""

from __future__ import annotations

from datetime import date

from hargaikan.data.weekly import catalog_overview, subtree_ids, week_start, weekly_medians
from hargaikan.domain.models import Location


def _loc(id_: str, level: str, parent: str | None) -> Location:
    return Location(id=id_, name=id_, level=level, parent_id=parent, lat=None, lng=None)  # type: ignore[arg-type]


LOCS = {
    "MY": _loc("MY", "nasional", None),
    "KL": _loc("KL", "negeri", "MY"),
    "P1": _loc("P1", "pasar", "KL"),
    "P2": _loc("P2", "premis", "KL"),
}


def test_week_start_is_monday() -> None:
    assert week_start(date(2025, 6, 15)) == date(2025, 6, 9)  # Sunday → Monday before
    assert week_start(date(2025, 6, 9)) == date(2025, 6, 9)


def test_subtree_ids_include_descendants() -> None:
    assert subtree_ids("KL", LOCS) == {"KL", "P1", "P2"}
    assert subtree_ids("MY", LOCS) == {"MY", "KL", "P1", "P2"}
    assert subtree_ids("P1", LOCS) == {"P1"}
    assert subtree_ids("UNKNOWN", LOCS) == {"UNKNOWN"}


def test_subtree_ids_terminate_on_cycle() -> None:
    cyc = {"A": _loc("A", "pasar", "B"), "B": _loc("B", "pasar", "A")}
    assert subtree_ids("A", cyc) == {"A", "B"}


def test_weekly_medians_weighted_and_sorted() -> None:
    points = [
        (date(2025, 6, 22), 30.0, 1),  # week of 16 Jun
        (date(2025, 6, 15), 10.0, 1),  # week of 9 Jun
        (date(2025, 6, 15), 20.0, 3),
        (date(2025, 6, 18), 40.0, 2),  # week of 16 Jun
    ]
    assert weekly_medians(points) == [
        (date(2025, 6, 9), 20.0, 4),  # multiset 10,20,20,20 → weighted median 20
        (date(2025, 6, 16), 40.0, 3),  # multiset 30,40,40 → weighted median 40
    ]


def test_weekly_medians_ignores_invalid_points_and_empty() -> None:
    assert weekly_medians([]) == []
    assert weekly_medians([(date(2025, 6, 15), 0.0, 1), (date(2025, 6, 15), 5.0, 0)]) == []


def test_catalog_overview_flags_synthetic() -> None:
    o = catalog_overview(3, 2, [date(2025, 1, 5), date(2025, 2, 2)], ["IKANSIGHT", "SYNTHETIC"])
    assert o == {"species_count": 3, "premise_count": 2, "latest_period": date(2025, 2, 2), "demo": True}
    assert catalog_overview(0, 0, [], [])["latest_period"] is None
    assert catalog_overview(1, 1, [], ["IKANSIGHT"])["demo"] is False
