"""CSV I/O for the cleaning pipeline (offline only: scripts and notebooks).

Reads IkanSight snapshot files into :class:`~hargaikan.pipeline.cleaning.RawRow` values and the
coverage file into a list of names. All parsing and classification stays in the pure
:mod:`hargaikan.pipeline.cleaning` module.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import pandas as pd

from hargaikan.pipeline.cleaning import RawRow, snapshot_index

PRICE_COLUMN = "harga_RM_per_kg"
REQUIRED_COLUMNS = ("item", "premise", PRICE_COLUMN, "bilangan_rekod", "tarikh_mula", "tarikh_akhir")
OPTIONAL_COLUMNS = ("harga_terendah", "harga_tertinggi", "harga_min", "harga_maks")
SNAPSHOT_GLOB = "ikansight_price_per_kg_by_location*.csv"
COVERAGE_FILE = "ikansight_fish_coverage.csv"
# Likely names of the fish column in the coverage file, in order of preference (case-insensitive).
COVERAGE_COLUMN_CANDIDATES = (
    "item", "species", "spesies", "nama_ikan", "ikan", "fish", "fish_name", "nama", "name", "species_name",
)


class SourceFormatError(ValueError):
    """A source file cannot be read as an IkanSight price snapshot."""


class UnexpectedUnitError(SourceFormatError):
    """The file has no ``harga_RM_per_kg`` column, so its unit cannot be confirmed as RM per kg."""


def _read_csv(path: Path) -> pd.DataFrame:
    # Read every value as text: cleaning decides what is numeric or a date. Blank cells become NaN.
    df = pd.read_csv(path, dtype=str, encoding="utf-8-sig", skipinitialspace=False)
    df.columns = [str(c).strip() for c in df.columns]
    return df


def _cell(value: object) -> str | None:
    return None if pd.isna(value) else str(value)  # type: ignore[arg-type]


def find_snapshot_files(raw_dir: str | Path) -> list[Path]:
    """Snapshot CSVs in ``raw_dir`` sorted by snapshot index, then name."""
    files = Path(raw_dir).glob(SNAPSHOT_GLOB)
    return sorted(files, key=lambda p: (snapshot_index(p.name), p.name))


def find_coverage_file(raw_dir: str | Path) -> Path | None:
    path = Path(raw_dir) / COVERAGE_FILE
    return path if path.is_file() else None


def read_price_snapshot(path: str | Path) -> list[RawRow]:
    """Read one snapshot file. The snapshot index comes from the file name ("x (2).csv" → 2).

    Raises UnexpectedUnitError (message contains "unexpected_unit" and the file name) if the
    ``harga_RM_per_kg`` column is missing, and SourceFormatError for other missing required columns.
    Missing optional columns (harga_terendah, harga_tertinggi, harga_min, harga_maks) read as None.
    """
    p = Path(path)
    df = _read_csv(p)
    if PRICE_COLUMN not in df.columns:
        raise UnexpectedUnitError(f"unexpected_unit: {p.name} has no {PRICE_COLUMN} column")
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise SourceFormatError(f"missing_columns: {p.name} lacks {', '.join(missing)}")
    snapshot = snapshot_index(p.name)
    rows: list[RawRow] = []
    for rec in df.to_dict(orient="records"):
        get = lambda c: _cell(rec[c]) if c in rec else None  # noqa: E731
        rows.append(
            RawRow(
                item=get("item") or "",
                premise=get("premise") or "",
                harga_RM_per_kg=get(PRICE_COLUMN),
                harga_terendah=get("harga_terendah"),
                harga_tertinggi=get("harga_tertinggi"),
                bilangan_rekod=get("bilangan_rekod"),
                tarikh_mula=get("tarikh_mula"),
                tarikh_akhir=get("tarikh_akhir"),
                harga_min=get("harga_min"),
                harga_maks=get("harga_maks"),
                snapshot=snapshot,
            )
        )
    return rows


def read_price_snapshots(paths: Iterable[str | Path]) -> list[RawRow]:
    """Read every snapshot file (see :func:`read_price_snapshot`) into one list of RawRows."""
    rows: list[RawRow] = []
    for path in paths:
        rows.extend(read_price_snapshot(path))
    return rows


def _coverage_column(df: pd.DataFrame) -> str | None:
    lower = {c.lower(): c for c in df.columns}
    for candidate in COVERAGE_COLUMN_CANDIDATES:
        if candidate in lower:
            return lower[candidate]
    for c in df.columns:  # tolerant fallback: first column that mostly holds non-numeric text
        values = df[c].dropna().astype(str).str.strip()
        values = values[values != ""]
        if len(values) and pd.to_numeric(values, errors="coerce").isna().mean() > 0.5:
            return c
    return None


def read_coverage(path: str | Path) -> list[str]:
    """Fish names from ikansight_fish_coverage.csv, as a cross-check only (never a price source).

    Picks the first column named like item/species/ikan/name, else the first mostly-text column.
    Returns unique, non-blank, stripped names in file order; an unrecognisable file gives [].
    """
    df = _read_csv(Path(path))
    column = _coverage_column(df)
    if column is None:
        return []
    names: dict[str, None] = {}
    for value in df[column].tolist():
        text = _cell(value)
        if text is not None and text.strip():
            names.setdefault(text.strip(), None)
    return list(names)
