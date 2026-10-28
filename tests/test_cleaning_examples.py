"""Unit tests for the cleaning pipeline and CSV reader using real IkanSight example rows."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pandas as pd
import pytest

from hargaikan.config import Settings
from hargaikan.domain.ids import location_id, species_id
from hargaikan.pipeline.cleaning import (
    clean,
    map_species,
    normalise_premise,
    parse_date,
    parse_item,
    snapshot_index,
    species_code,
    to_price_records,
    with_unmatched,
)
from hargaikan.pipeline.sources import (
    SourceFormatError,
    UnexpectedUnitError,
    find_snapshot_files,
    read_coverage,
    read_price_snapshots,
)

ITEM = "IKAN BAWAL HITAM (ANTARA 2 HINGGA 5 EKOR SEKILO)"
COLUMNS = [
    "item", "premise", "harga_RM_per_kg", "harga_terendah", "harga_tertinggi",
    "bilangan_rekod", "tarikh_mula", "tarikh_akhir", "harga_min", "harga_maks",
]
# Real example rows. Dates/counts not given in the source examples use the same week.
EXAMPLE_ROWS = [
    [ITEM, "AEON ( AU2 )", "48.9", "48.9", "48.9", "2", "19/10/2026", "26/10/2026", "48.9", "48.9"],
    [ITEM, "AEON BIG ( KEPONG )", "29.9", "29.9", "29.9", "1", "26/10/2026", "26/10/2026", "", ""],
    [ITEM, "AEON MAXVALU PRIME ( DESA PARK CITY )", "45.9", "", "", "3", "19/10/2026", "26/10/2026", "", ""],
    [ITEM, "JAYA GROCER ( INTERMARK MALL )", "39.9", "39.9", "39.9", "3", "12/10/2026", "26/10/2026", "", ""],
    [ITEM, "NSK GROCER ( TIMES SQUARE )", "21.47", "18.8", "22.8", "3", "19/10/2026", "26/10/2026", "18.8", "22.8"],
    [ITEM, "MY MYDIN ( DANAU SAUJANA )", "22.9", "", "", "1", "19/10/2026", "26/10/2026", "", ""],
]


def _write_csv(path: Path, rows: list[list[str]], columns: list[str] = COLUMNS) -> Path:
    pd.DataFrame(rows, columns=columns).to_csv(path, index=False)
    return path


# --- Parsing helpers ---------------------------------------------------------------------------


def test_parse_item_real_example() -> None:
    assert parse_item(ITEM) == ("IKAN BAWAL HITAM", "ANTARA 2 HINGGA 5 EKOR SEKILO")
    assert parse_item("  ikan kembung ") == ("IKAN KEMBUNG", None)
    assert parse_item("IKAN TONGKOL ()") == ("IKAN TONGKOL", None)


@pytest.mark.parametrize(
    ("raw", "chain", "branch", "display"),
    [
        ("AEON ( AU2 )", "AEON", "AU2", "AEON (AU2)"),
        ("AEON BIG ( KEPONG )", "AEON BIG", "KEPONG", "AEON BIG (KEPONG)"),
        ("AEON MAXVALU PRIME ( DESA PARK CITY )", "AEON MAXVALU PRIME", "DESA PARK CITY", "AEON MAXVALU PRIME (DESA PARK CITY)"),
        ("JAYA GROCER ( INTERMARK MALL )", "JAYA GROCER", "INTERMARK MALL", "JAYA GROCER (INTERMARK MALL)"),
        ("NSK GROCER ( TIMES SQUARE )", "NSK GROCER", "TIMES SQUARE", "NSK GROCER (TIMES SQUARE)"),
        ("MY MYDIN ( DANAU SAUJANA )", "MY MYDIN", "DANAU SAUJANA", "MY MYDIN (DANAU SAUJANA)"),
        ("aeon(au2)", "AEON", "AU2", "AEON (AU2)"),
        ("MYDIN", "MYDIN", None, "MYDIN"),
    ],
)
def test_normalise_premise_real_examples(raw: str, chain: str, branch: str | None, display: str) -> None:
    p = normalise_premise(raw)
    assert (p.chain, p.branch, p.display, p.key) == (chain, branch, display, display)


@pytest.mark.parametrize(
    "value",
    ["19/10/2026", "2026-10-19", "2026-10-19 00:00:00", " 19/10/2026 ", date(2026, 10, 19), datetime(2026, 10, 19, 8, 30),
     pd.Timestamp("2026-10-19")],
)
def test_parse_date_formats(value: object) -> None:
    assert parse_date(value) == date(2026, 10, 19)


def test_slash_dates_are_never_month_first() -> None:
    assert parse_date("01/02/2026") == date(2026, 2, 1)
    assert parse_date("10/19/2026") is None  # would only be valid as MM/DD


@pytest.mark.parametrize("value", [None, float("nan"), pd.NaT, "", "bukan tarikh", "31/02/2026", "2026/10/19"])
def test_parse_date_rejects(value: object) -> None:
    assert parse_date(value) is None


@pytest.mark.parametrize(
    ("name", "index"),
    [
        ("ikansight_price_per_kg_by_location.csv", 0),
        ("ikansight_price_per_kg_by_location (2).csv", 2),
        ("ikansight_price_per_kg_by_location (13).CSV", 13),
    ],
)
def test_snapshot_index(name: str, index: int) -> None:
    assert snapshot_index(name) == index


def test_species_code_and_ids() -> None:
    assert species_code("IKAN BAWAL HITAM") == "IKAN_BAWAL_HITAM"
    assert species_code("  ikan  bawal-hitam ") == "IKAN_BAWAL_HITAM"
    assert species_id("IKAN_BAWAL_HITAM") == species_id("IKAN_BAWAL_HITAM")
    assert species_id("IKAN_BAWAL_HITAM") != species_id("IKAN_BAWAL_PUTIH")
    assert location_id("premis", "AEON (AU2)") != location_id("pasar", "AEON (AU2)")


# --- CSV reader --------------------------------------------------------------------------------


def test_read_price_snapshots_real_columns(tmp_path: Path) -> None:
    path = _write_csv(tmp_path / "ikansight_price_per_kg_by_location.csv", EXAMPLE_ROWS)
    rows = read_price_snapshots([path])
    assert len(rows) == 6
    first = rows[0]
    assert first.item == ITEM
    assert first.premise == "AEON ( AU2 )"
    assert first.harga_RM_per_kg == "48.9"
    assert first.bilangan_rekod == "2"
    assert (first.tarikh_mula, first.tarikh_akhir) == ("19/10/2026", "26/10/2026")
    assert first.snapshot == 0
    assert rows[1].harga_min is None and rows[1].harga_maks is None  # blank cells → None


def test_read_price_snapshots_without_optional_columns(tmp_path: Path) -> None:
    columns = [c for c in COLUMNS if c not in ("harga_min", "harga_maks")]
    rows = [r[:8] for r in EXAMPLE_ROWS[:2]]
    path = _write_csv(tmp_path / "ikansight_price_per_kg_by_location (3).csv", rows, columns)
    raw = read_price_snapshots([path])
    assert all(r.harga_min is None and r.harga_maks is None for r in raw)
    assert {r.snapshot for r in raw} == {3}


def test_file_without_price_column_is_rejected(tmp_path: Path) -> None:
    columns = ["item", "premise", "harga_RM", "bilangan_rekod", "tarikh_mula", "tarikh_akhir"]
    path = _write_csv(tmp_path / "ikansight_price_by_location.csv", [[ITEM, "AEON ( AU2 )", "48.9", "2", "19/10/2026", "26/10/2026"]], columns)
    with pytest.raises(UnexpectedUnitError) as exc:
        read_price_snapshots([path])
    assert "unexpected_unit" in str(exc.value)
    assert "ikansight_price_by_location.csv" in str(exc.value)


def test_file_missing_other_required_column_is_rejected(tmp_path: Path) -> None:
    columns = ["item", "harga_RM_per_kg", "bilangan_rekod", "tarikh_mula", "tarikh_akhir"]
    path = _write_csv(tmp_path / "x.csv", [[ITEM, "48.9", "2", "19/10/2026", "26/10/2026"]], columns)
    with pytest.raises(SourceFormatError, match="premise"):
        read_price_snapshots([path])


def test_find_snapshot_files_orders_by_index(tmp_path: Path) -> None:
    for name in ["ikansight_price_per_kg_by_location (2).csv", "ikansight_price_per_kg_by_location.csv",
                 "ikansight_price_per_kg_by_location (1).csv", "ikansight_fish_coverage.csv"]:
        _write_csv(tmp_path / name, EXAMPLE_ROWS[:1])
    assert [snapshot_index(p.name) for p in find_snapshot_files(tmp_path)] == [0, 1, 2]


def test_read_coverage(tmp_path: Path) -> None:
    path = tmp_path / "ikansight_fish_coverage.csv"
    pd.DataFrame({"item": [ITEM, "IKAN KEMBUNG", "IKAN KEMBUNG", ""], "bilangan_premis": [6, 2, 2, 0]}).to_csv(path, index=False)
    assert read_coverage(path) == [ITEM, "IKAN KEMBUNG"]


def test_read_coverage_tolerates_other_column_names(tmp_path: Path) -> None:
    path = tmp_path / "ikansight_fish_coverage.csv"
    pd.DataFrame({"n": [1, 2], "Jenis Ikan": ["IKAN A", "IKAN B"]}).to_csv(path, index=False)
    assert read_coverage(path) == ["IKAN A", "IKAN B"]


# --- End to end: read → clean → map → records ----------------------------------------------------


def test_clean_real_example_rows(tmp_path: Path) -> None:
    path = _write_csv(tmp_path / "ikansight_price_per_kg_by_location.csv", EXAMPLE_ROWS)
    cleaned, report = clean(read_price_snapshots([path]), Settings(), coverage_names=[ITEM, "IKAN KEMBUNG"])

    assert [r.quality_status for r in cleaned] == ["ok"] * 6
    assert {r.species_name for r in cleaned} == {"IKAN BAWAL HITAM"}
    assert {r.grade for r in cleaned} == {"ANTARA 2 HINGGA 5 EKOR SEKILO"}
    by_premise = {r.premise.display: r for r in cleaned}
    aeon = by_premise["AEON (AU2)"]
    assert (aeon.price_rm_per_kg, aeon.record_count) == (48.9, 2)
    assert (aeon.date_start, aeon.date_end) == (date(2026, 10, 19), date(2026, 10, 26))
    assert by_premise["AEON BIG (KEPONG)"].date_start == date(2026, 10, 26)
    assert by_premise["JAYA GROCER (INTERMARK MALL)"].date_start == date(2026, 10, 12)
    nsk = by_premise["NSK GROCER (TIMES SQUARE)"]
    assert (nsk.price_rm_per_kg, nsk.price_low, nsk.price_high, nsk.record_count) == (21.47, 18.8, 22.8, 3)
    assert by_premise["MY MYDIN (DANAU SAUJANA)"].price_rm_per_kg == 22.9

    assert report.rows_in == report.rows_out == 6
    assert (report.duplicates, report.conflicts, report.unrepresentable) == (0, 0, 0)
    assert report.by_status["ok"] == 6 and sum(report.by_status.values()) == 6
    assert report.coverage_without_prices == ("IKAN KEMBUNG",)

    assignments, new_species, new_aliases = map_species(cleaned, [], [])
    assert [(s.code, s.canonical_name_ms, s.active) for s in new_species] == [("IKAN_BAWAL_HITAM", "IKAN BAWAL HITAM", True)]
    assert new_species[0].id == species_id("IKAN_BAWAL_HITAM")
    assert [(a.alias, a.language) for a in new_aliases] == [(ITEM, "raw")]
    assert with_unmatched(report, new_species).unmatched_species == ("IKAN BAWAL HITAM",)

    records = to_price_records(assignments)
    assert len(records) == 6
    rec = next(r for r in records if r.location_id == location_id("premis", "AEON (AU2)"))
    assert (rec.unit, rec.raw_item_name, rec.source_name, rec.source_snapshot) == ("kg", ITEM, "IKANSIGHT", 0)
    assert (rec.price_rm_per_kg, rec.record_count, rec.quality_status) == (48.9, 2, "ok")

    # Re-running with the created species as catalog matches it via the raw alias: nothing new.
    again, more_species, more_aliases = map_species(cleaned, new_species, new_aliases)
    assert (more_species, more_aliases) == ([], [])
    assert {sid for _, sid in again} == {new_species[0].id}


def test_dedupe_across_snapshots_keeps_highest(tmp_path: Path) -> None:
    base = _write_csv(tmp_path / "ikansight_price_per_kg_by_location.csv", EXAMPLE_ROWS)
    changed = [r.copy() for r in EXAMPLE_ROWS[:2]]
    changed[0][2] = "47.9"  # AEON (AU2) price revised in the newer snapshot
    changed[0][1] = "AEON (AU2)"  # whitespace variant of the same premise
    changed[1][6], changed[1][7] = "2026-10-26", "2026-10-26 00:00:00"  # ISO / pandas dates, same values
    newer = _write_csv(tmp_path / "ikansight_price_per_kg_by_location (2).csv", changed)

    cleaned, report = clean(read_price_snapshots([base, newer]), Settings())
    assert (report.rows_in, report.rows_out, report.duplicates, report.conflicts) == (8, 6, 2, 1)
    aeon = next(r for r in cleaned if r.premise.key == "AEON (AU2)")
    assert (aeon.price_rm_per_kg, aeon.snapshot) == (47.9, 2)
    assert next(r for r in cleaned if r.premise.key == "AEON BIG (KEPONG)").snapshot == 2


def test_non_ok_rows_keep_status_and_unrepresentable_rows_are_counted(tmp_path: Path) -> None:
    rows = [
        *EXAMPLE_ROWS,
        ["IKAN KEMBUNG", "AEON ( AU2 )", "12.9", "15.0", "", "2", "19/10/2026", "26/10/2026", "", ""],  # low > price
        ["IKAN KEMBUNG", "AEON BIG ( KEPONG )", "abc", "", "", "2", "19/10/2026", "26/10/2026", "", ""],
        ["IKAN KEMBUNG", "MY MYDIN ( DANAU SAUJANA )", "12.9", "", "", "0", "19/10/2026", "26/10/2026", "", ""],
        ["IKAN KEMBUNG", "NSK GROCER ( TIMES SQUARE )", "12.9", "", "", "1", "26/10/2026", "19/10/2026", "", ""],
        ["IKAN KEMBUNG", "JAYA GROCER ( INTERMARK MALL )", "0.5", "", "", "1", "19/10/2026", "26/10/2026", "", ""],
    ]
    path = _write_csv(tmp_path / "ikansight_price_per_kg_by_location.csv", rows)
    cleaned, report = clean(read_price_snapshots([path]), Settings())
    statuses = [r.quality_status for r in cleaned if r.species_name == "IKAN KEMBUNG"]
    assert statuses == ["inconsistent_range", "invalid_price", "invalid_count", "invalid_date", "outlier"]
    assert report.unrepresentable == 3

    assignments, _, _ = map_species(cleaned, [], [])
    records = to_price_records(assignments)
    assert len(records) == 6 + 2
    assert sorted(r.quality_status for r in records if r.raw_item_name == "IKAN KEMBUNG") == ["inconsistent_range", "outlier"]


def test_outlier_fence_needs_four_rows() -> None:
    from hargaikan.pipeline.cleaning import RawRow

    def raw(price: str, premise: str) -> RawRow:
        return RawRow("IKAN A", premise, price, None, None, "1", "19/10/2026", "26/10/2026", None, None, 0)

    three = [raw("10", "P1"), raw("11", "P2"), raw("200", "P3")]
    assert [r.quality_status for r in clean(three, Settings())[0]] == ["ok", "ok", "ok"]
    five = [raw("10", "P1"), raw("11", "P2"), raw("12", "P3"), raw("11.5", "P4"), raw("200", "P5")]
    assert [r.quality_status for r in clean(five, Settings())[0]] == ["ok", "ok", "ok", "ok", "outlier"]
