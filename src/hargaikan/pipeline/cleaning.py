"""Cleaning pipeline: parse, deduplicate and classify raw IkanSight rows, then map species.

Pure logic (layer 1): no file or network I/O and no supabase, pinecone, requests or streamlit
imports. CSV reading lives in :mod:`hargaikan.pipeline.sources`.

Flow: ``read_price_snapshots`` (sources) → :func:`clean` → :func:`map_species` →
:func:`to_price_records`. Unit is fixed to ``kg`` because the only accepted price column is
``harga_RM_per_kg``.
"""

from __future__ import annotations

import hashlib
import math
import numbers
import re
import unicodedata
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime
from typing import get_args

import numpy as np

from hargaikan.config import Settings
from hargaikan.core.text import normalise_name, normalise_term
from hargaikan.domain.ids import location_id as _location_id
from hargaikan.domain.ids import species_id as _species_id
from hargaikan.domain.models import PriceRecord, QualityStatus, Species, SpeciesAlias

DEFAULT_SOURCE_NAME = "IKANSIGHT"
QUALITY_STATUSES: tuple[str, ...] = get_args(QualityStatus)
# Statuses whose rows cannot satisfy the price_records constraints (price > 0, count ≥ 1, dates).
UNREPRESENTABLE_STATUSES = frozenset({"invalid_date", "invalid_price", "invalid_count"})


# --- Data shapes -------------------------------------------------------------------------------


@dataclass(frozen=True)
class RawRow:
    """One CSV row as read, values untouched. ``snapshot``: 0 = base file, n = "(n)" copy."""

    item: str
    premise: str
    harga_RM_per_kg: object
    harga_terendah: object
    harga_tertinggi: object
    bilangan_rekod: object
    tarikh_mula: object
    tarikh_akhir: object
    harga_min: object
    harga_maks: object
    snapshot: int


@dataclass(frozen=True)
class ParsedPremise:
    key: str
    chain: str
    branch: str | None
    display: str  # "AEON (AU2)"


@dataclass(frozen=True)
class CleanRow:
    species_name: str
    grade: str | None
    premise: ParsedPremise
    raw_item_name: str
    price_rm_per_kg: float | None
    price_low: float | None
    price_high: float | None
    price_min: float | None
    price_max: float | None
    record_count: int | None
    date_start: date | None
    date_end: date | None
    snapshot: int
    quality_status: QualityStatus


@dataclass(frozen=True)
class IngestReport:
    rows_in: int
    rows_out: int
    duplicates: int
    conflicts: int
    by_status: dict[str, int]
    unmatched_species: tuple[str, ...]
    coverage_without_prices: tuple[str, ...]
    # Rows that keep their status in the report but cannot be stored as PriceRecords because the
    # price_records table requires price > 0, record_count ≥ 1 and valid dates (see to_price_records).
    unrepresentable: int = 0


# --- Item, premise, date and snapshot parsing --------------------------------------------------

ITEM_RE = re.compile(r"^\s*(?P<species>[^(]+?)\s*(?:\((?P<grade>[^)]*)\))?\s*$")
_PAREN_TRIM_RE = re.compile(r"\(\s*(.*?)\s*\)")
_PREMISE_RE = re.compile(r"^(?P<chain>[^(]+?)\s*(?:\((?P<branch>[^)]*)\))?$")
_SNAPSHOT_RE = re.compile(r"\((\d+)\)\s*\.csv\s*$", re.IGNORECASE)
_DATE_FORMATS = (
    "%d/%m/%Y",  # slash dates are always DD/MM/YYYY, never MM/DD
    "%Y-%m-%d",
    "%Y-%m-%d %H:%M:%S",  # pandas datetime strings, e.g. "2026-10-19 00:00:00"
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S.%f",
    "%Y-%m-%dT%H:%M:%S.%f",
)


def parse_item(item: str) -> tuple[str, str | None]:
    """Split "SPECIES (GRADE)" into (species, grade), both normalised. No suffix → grade None.

    "IKAN BAWAL HITAM (ANTARA 2 HINGGA 5 EKOR SEKILO)" → ("IKAN BAWAL HITAM", "ANTARA 2 HINGGA 5 EKOR SEKILO").
    Unusual shapes (e.g. text after the closing parenthesis) keep the whole string as the name.
    """
    m = ITEM_RE.match(item)
    if m is None:
        return normalise_name(item), None
    g = m.group("grade")
    return normalise_name(m.group("species")), (normalise_name(g) if g and g.strip() else None)


def normalise_premise(premise: str) -> ParsedPremise:
    """"AEON ( AU2 )" → chain "AEON", branch "AU2", display and key "AEON (AU2)"."""
    text = _PAREN_TRIM_RE.sub(r"(\1)", normalise_name(premise))
    m = _PREMISE_RE.match(text)
    chain = m.group("chain").strip() if m else text
    branch = ((m.group("branch") or "").strip() or None) if m else None
    display = f"{chain} ({branch})" if branch else chain
    return ParsedPremise(key=display, chain=chain, branch=branch, display=display)


def _is_missing(value: object) -> bool:
    if value is None:
        return True
    try:
        return bool(value != value)  # NaN, pandas NaT
    except Exception:
        return False


def parse_date(value: object) -> date | None:
    """DD/MM/YYYY, YYYY-MM-DD (optionally with a time, as pandas writes it), date or datetime.

    Returns None for anything else (the row then becomes ``invalid_date``).
    """
    if _is_missing(value):
        return None
    if isinstance(value, date):
        return value.date() if isinstance(value, datetime) else value
    s = str(value).strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


def snapshot_index(filename: str) -> int:
    """"x.csv" → 0, "x (2).csv" → 2."""
    m = _SNAPSHOT_RE.search(filename)
    return int(m.group(1)) if m else 0


# --- Value coercion ------------------------------------------------------------------------------


def _text(value: object) -> str:
    return "" if _is_missing(value) else str(value)


def _to_float(value: object) -> float | None:
    """Number or numeric string → float (may be inf/NaN). Missing, blank or non-numeric → None."""
    if _is_missing(value) or isinstance(value, bool):
        return None
    if isinstance(value, numbers.Real):
        return float(value)
    s = str(value).strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _to_aux(value: object) -> float | None:
    """Auxiliary prices (low/high/min/max): like _to_float, but NaN counts as missing."""
    v = _to_float(value)
    return None if v is None or math.isnan(v) else v


def _to_count(value: object) -> int | None:
    """Integer or integral number/string ("3", "3.0", 3.0) → int. Anything else → None."""
    if _is_missing(value) or isinstance(value, bool):
        return None
    if isinstance(value, numbers.Integral):
        return int(value)
    v = _to_float(value)
    if v is None or not math.isfinite(v) or not v.is_integer():
        return None
    return int(v)


def _is_finite_number(x: object) -> bool:
    return isinstance(x, numbers.Real) and not isinstance(x, bool) and math.isfinite(float(x))


def _is_int(x: object) -> bool:
    return isinstance(x, numbers.Integral) and not isinstance(x, bool)


# --- Dedupe --------------------------------------------------------------------------------------

_VALUE_FIELD_NAMES = ("harga_RM_per_kg", "harga_terendah", "harga_tertinggi", "bilangan_rekod", "harga_min", "harga_maks")


def _canon(value: object) -> tuple[int, float, str]:
    """Totally ordered representation: missing < numbers (by value) < other text."""
    if _is_missing(value) or (isinstance(value, str) and not value.strip()):
        return (0, 0.0, "")
    v = _to_float(value)
    if v is not None and not math.isnan(v):
        return (1, v, "")
    if v is not None:  # "nan" text
        return (0, 0.0, "")
    return (2, 0.0, str(value).strip())


def value_fields(r: RawRow) -> tuple[tuple[int, float, str], ...]:
    """The price/count/range values of a row, comparable across types ("48.9" == 48.9)."""
    return tuple(_canon(getattr(r, f)) for f in _VALUE_FIELD_NAMES)


def dedupe_key(r: RawRow) -> tuple[str, str, date | None, date | None]:
    """Dedupe_Key = (normalised item, premise key, tarikh_mula, tarikh_akhir)."""
    return (
        normalise_name(_text(r.item)),
        normalise_premise(_text(r.premise)).key,
        parse_date(r.tarikh_mula),
        parse_date(r.tarikh_akhir),
    )


def _preference(r: RawRow) -> tuple:  # type: ignore[type-arg]
    """(snapshot, value_fields) as in the design, then the raw text of every field as a final
    tie-break, so the kept row never depends on input order (even for whitespace variants)."""
    raw = tuple(repr(getattr(r, f)) for f in RawRow.__dataclass_fields__)
    return (r.snapshot, value_fields(r), raw)


def dedupe(rows: Sequence[RawRow]) -> tuple[list[RawRow], int, int]:
    """Keep one row per Dedupe_Key: the one with the greatest (snapshot, value_fields).

    Returns (rows, duplicates, conflicts). ``duplicates`` counts every dropped row; ``conflicts``
    counts duplicates whose value fields differ from the row kept so far.
    """
    best: dict[tuple[str, str, date | None, date | None], RawRow] = {}
    duplicates = conflicts = 0
    for r in rows:
        k = dedupe_key(r)
        if k not in best:
            best[k] = r
            continue
        duplicates += 1
        if value_fields(best[k]) != value_fields(r):
            conflicts += 1
        if _preference(r) > _preference(best[k]):
            best[k] = r
    return list(best.values()), duplicates, conflicts


# --- Classification ------------------------------------------------------------------------------


def _basic_status(row: CleanRow) -> QualityStatus | None:
    """The first four checks in rule order; None if the row passes all of them."""
    if row.date_start is None or row.date_end is None or row.date_start > row.date_end:
        return "invalid_date"
    if not _is_finite_number(row.price_rm_per_kg) or row.price_rm_per_kg <= 0:  # type: ignore[operator]
        return "invalid_price"
    if not (_is_int(row.record_count) and row.record_count >= 1):  # type: ignore[operator]
        return "invalid_count"
    price = row.price_rm_per_kg
    if (
        (row.price_low is not None and row.price_low > price)
        or (row.price_high is not None and price > row.price_high)
        or (row.price_min is not None and row.price_max is not None and row.price_min > row.price_max)
    ):
        return "inconsistent_range"
    return None


def outlier_fences(rows: Sequence[CleanRow], k: float) -> dict[str, tuple[float, float]]:
    """Per-species fence [Q1 − k·IQR, Q3 + k·IQR] from rows that pass the first four checks.

    Quartiles use linear interpolation (numpy default). Species with fewer than 4 such rows get no fence.
    """
    by_species: dict[str, list[float]] = {}
    for r in rows:
        if _basic_status(r) is None:
            by_species.setdefault(r.species_name, []).append(float(r.price_rm_per_kg))  # type: ignore[arg-type]
    fences: dict[str, tuple[float, float]] = {}
    for name, values in by_species.items():
        if len(values) < 4:
            continue
        q1, q3 = (float(q) for q in np.percentile(np.asarray(values, dtype=float), [25, 75]))
        iqr = q3 - q1
        fences[name] = (q1 - k * iqr, q3 + k * iqr)
    return fences


def classify(row: CleanRow, fences: Mapping[str, tuple[float, float]], s: Settings) -> QualityStatus:
    """Rule order: invalid_date, invalid_price, invalid_count, inconsistent_range, outlier, ok."""
    basic = _basic_status(row)
    if basic is not None:
        return basic
    price = float(row.price_rm_per_kg)  # type: ignore[arg-type]
    lo, hi = fences.get(row.species_name, (-math.inf, math.inf))
    if not (s.min_price_rm_per_kg <= price <= s.max_price_rm_per_kg) or not (lo <= price <= hi):
        return "outlier"
    return "ok"


def is_representable(row: CleanRow) -> bool:
    """True if the row satisfies the price_records constraints: finite price > 0, integer
    record_count ≥ 1, both dates present and date_start ≤ date_end."""
    return (
        _is_finite_number(row.price_rm_per_kg)
        and row.price_rm_per_kg > 0  # type: ignore[operator]
        and _is_int(row.record_count)
        and row.record_count >= 1  # type: ignore[operator]
        and row.date_start is not None
        and row.date_end is not None
        and row.date_start <= row.date_end
    )


def _to_clean_row(r: RawRow) -> CleanRow:
    raw_item = _text(r.item)
    species, grade = parse_item(raw_item)
    return CleanRow(
        species_name=species,
        grade=grade,
        premise=normalise_premise(_text(r.premise)),
        raw_item_name=raw_item,
        price_rm_per_kg=_to_float(r.harga_RM_per_kg),
        price_low=_to_aux(r.harga_terendah),
        price_high=_to_aux(r.harga_tertinggi),
        price_min=_to_aux(r.harga_min),
        price_max=_to_aux(r.harga_maks),
        record_count=_to_count(r.bilangan_rekod),
        date_start=parse_date(r.tarikh_mula),
        date_end=parse_date(r.tarikh_akhir),
        snapshot=r.snapshot,
        quality_status="ok",  # placeholder; set by classify below
    )


def clean(
    rows: Sequence[RawRow], s: Settings, coverage_names: Iterable[str] | None = None
) -> tuple[list[CleanRow], IngestReport]:
    """Dedupe, parse and classify raw rows. Every kept row is returned with its Quality_Status.

    ``coverage_names`` (from ikansight_fish_coverage.csv) is a cross-check only: species named
    there without any price row are listed in ``coverage_without_prices``. ``unmatched_species``
    is left empty here; fill it after :func:`map_species` with :func:`with_unmatched`.
    """
    kept, duplicates, conflicts = dedupe(rows)
    parsed = [_to_clean_row(r) for r in kept]
    fences = outlier_fences(parsed, s.outlier_iqr_k)
    out = [replace(r, quality_status=classify(r, fences, s)) for r in parsed]

    by_status = {status: 0 for status in QUALITY_STATUSES}
    for r in out:
        by_status[r.quality_status] += 1

    coverage_without_prices: tuple[str, ...] = ()
    if coverage_names is not None:
        present = {normalise_term(r.species_name) for r in out}
        missing: dict[str, str] = {}
        for name in coverage_names:
            if _is_missing(name) or not str(name).strip():
                continue
            species, _ = parse_item(str(name))
            term = normalise_term(species)
            if term and term not in present:
                missing.setdefault(term, species)
        coverage_without_prices = tuple(sorted(missing.values()))

    report = IngestReport(
        rows_in=len(rows),
        rows_out=len(out),
        duplicates=duplicates,
        conflicts=conflicts,
        by_status=by_status,
        unmatched_species=(),
        coverage_without_prices=coverage_without_prices,
        unrepresentable=sum(1 for r in out if not is_representable(r)),
    )
    return out, report


# --- Species catalog mapping ---------------------------------------------------------------------


def _short_hash(text: str, n: int) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:n].upper()


def species_code(name: str) -> str:
    """Deterministic slug: "IKAN BAWAL HITAM" → "IKAN_BAWAL_HITAM".

    Accents are dropped (NFKD → ASCII) and every run of other characters becomes "_". A name with
    no ASCII letters or digits gets "SPECIES_" plus a hash of its normalised form.
    """
    n = normalise_name(name)
    ascii_text = unicodedata.normalize("NFKD", n).encode("ascii", "ignore").decode("ascii").upper()
    slug = re.sub(r"[^A-Z0-9]+", "_", ascii_text).strip("_")
    return slug or f"SPECIES_{_short_hash(normalise_term(n), 10)}"


def _build_lookup(species: Sequence[Species], aliases: Sequence[SpeciesAlias]) -> tuple[dict[str, str], dict[str, str]]:
    """(alias term → species id, canonical-name term → species id). Ties pick the smallest id."""
    alias_lookup: dict[str, str] = {}
    for a in aliases:
        t = normalise_term(a.alias)
        if t and (t not in alias_lookup or a.species_id < alias_lookup[t]):
            alias_lookup[t] = a.species_id
    canonical_lookup: dict[str, str] = {}
    for sp in species:
        t = normalise_term(sp.canonical_name_ms)
        if t and (t not in canonical_lookup or sp.id < canonical_lookup[t]):
            canonical_lookup[t] = sp.id
    return alias_lookup, canonical_lookup


def map_species(
    rows: Sequence[CleanRow], species: Sequence[Species], aliases: Sequence[SpeciesAlias]
) -> tuple[list[tuple[CleanRow, str]], list[Species], list[SpeciesAlias]]:
    """Assign a species id to every row; create species for names that match nothing.

    Matching uses ``normalise_term`` and tries, in order:
      1. the parsed species name against the aliases (Requirement 4.1),
      2. the raw item name against the aliases (the ``raw`` aliases this function creates),
      3. the parsed species name against canonical_name_ms (catalog names act as implicit aliases).
    Steps 2–3 make re-runs idempotent: a species created earlier is found again instead of duplicated.

    Each distinct unmatched normalised name creates one active species (canonical_name_ms = the
    parsed name, ``code = species_code(name)``, id = uuid5(NAMESPACE, code)) and one ``raw`` alias per
    distinct raw item name. If the code is already used by another species, a short hash of the name
    is appended. Unmatched names are processed in sorted order, so the result is independent of row
    order and identical for identical inputs.

    Returns (rows with species ids in input order, new species sorted by code, new aliases).
    """
    alias_lookup, canonical_lookup = _build_lookup(species, aliases)

    def match(r: CleanRow) -> str | None:
        name_term = normalise_term(r.species_name)
        return (
            alias_lookup.get(name_term)
            or alias_lookup.get(normalise_term(r.raw_item_name))
            or canonical_lookup.get(name_term)
        )

    matched: dict[int, str] = {}
    unmatched: dict[str, list[CleanRow]] = {}
    for i, r in enumerate(rows):
        sid = match(r)
        if sid is not None:
            matched[i] = sid
        else:
            unmatched.setdefault(normalise_term(r.species_name), []).append(r)

    taken_codes = {sp.code for sp in species}
    term_to_id: dict[str, str] = {}
    new_species: list[Species] = []
    new_aliases: list[SpeciesAlias] = []
    for term in sorted(unmatched):
        group = unmatched[term]
        canonical = min(r.species_name for r in group)
        code = species_code(canonical)
        if code in taken_codes:
            code = f"{code}_{_short_hash(term, 8)}"
            n = 2
            while code in taken_codes:  # practically unreachable; keeps codes unique
                code = f"{species_code(canonical)}_{_short_hash(term, 8)}_{n}"
                n += 1
        taken_codes.add(code)
        sid = _species_id(code)
        term_to_id[term] = sid
        new_species.append(
            Species(id=sid, code=code, canonical_name_ms=canonical, canonical_name_en=None, allergen_groups=(), active=True)
        )
        raw_by_term: dict[str, str] = {}
        for r in group:
            t = normalise_term(r.raw_item_name)
            if t and (t not in raw_by_term or r.raw_item_name < raw_by_term[t]):
                raw_by_term[t] = r.raw_item_name
        if not raw_by_term:  # blank raw names: fall back to the parsed name so the alias exists
            raw_by_term[term] = canonical
        new_aliases.extend(SpeciesAlias(species_id=sid, alias=raw_by_term[t], language="raw") for t in sorted(raw_by_term))

    assignments = [
        (r, matched[i] if i in matched else term_to_id[normalise_term(r.species_name)]) for i, r in enumerate(rows)
    ]
    new_species.sort(key=lambda sp: sp.code)
    return assignments, new_species, new_aliases


def with_unmatched(report: IngestReport, new_species: Sequence[Species]) -> IngestReport:
    """Copy of ``report`` listing the canonical names of species created by :func:`map_species`."""
    return replace(report, unmatched_species=tuple(sorted(sp.canonical_name_ms for sp in new_species)))


# --- PriceRecord conversion ----------------------------------------------------------------------


def default_location_id(premise: ParsedPremise) -> str:
    """Location id of a supermarket branch: uuid5 of ("premis", premise key)."""
    return _location_id("premis", premise.key)


def to_price_records(
    rows_with_ids: Sequence[tuple[CleanRow, str]],
    source_name: str = DEFAULT_SOURCE_NAME,
    location_id_for: Callable[[ParsedPremise], str] = default_location_id,
) -> list[PriceRecord]:
    """Convert mapped CleanRows (ok and non-ok) to PriceRecords with unit "kg".

    Each record keeps the raw item name, grade, source name, snapshot index, dates and
    Quality_Status of its row. Design decision: PriceRecord (and the price_records table) require a
    finite price > 0, an integer record_count ≥ 1 and valid dates with date_start ≤ date_end. Rows
    that cannot meet that (exactly the invalid_date, invalid_price and invalid_count rows) are
    skipped here rather than stored with invented values. They are still counted per status in the
    IngestReport and in ``IngestReport.unrepresentable``. ok, inconsistent_range and outlier rows are
    always converted, in input order.
    """
    records: list[PriceRecord] = []
    for row, sid in rows_with_ids:
        if not is_representable(row):
            continue
        records.append(
            PriceRecord(
                species_id=sid,
                location_id=location_id_for(row.premise),
                raw_item_name=row.raw_item_name,
                grade=row.grade,
                unit="kg",
                price_rm_per_kg=float(row.price_rm_per_kg),  # type: ignore[arg-type]
                price_low=row.price_low,
                price_high=row.price_high,
                record_count=int(row.record_count),  # type: ignore[arg-type]
                date_start=row.date_start,  # type: ignore[arg-type]
                date_end=row.date_end,  # type: ignore[arg-type]
                source_name=source_name,
                source_snapshot=row.snapshot,
                quality_status=row.quality_status,
            )
        )
    return records
