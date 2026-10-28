"""Offline ingest orchestration shared by ``scripts/ingest.py`` and ``notebooks/01_ingest.ipynb``.

This module does I/O (reads CSVs and the geocode cache, talks to Supabase through a
PublishRepository); all decisions live in the pure modules it calls:

    read (sources) → clean → map species → hierarchy (locations) → to_price_records
    → build_bands → Batch → publish (stage → validate → promote) → report

``--synthetic`` replaces the price records with seeded SYNTHETIC demo records (development and
test only; the publisher also refuses SYNTHETIC data for production).

Exit codes of :func:`main`: 0 success, 1 pipeline/publish failure, 2 usage or configuration error
(target mismatch, missing secret, --synthetic for production).
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any, TextIO

from hargaikan.config import Settings, load_settings
from hargaikan.domain.models import PriceRecord, Species, SpeciesAlias
from hargaikan.pipeline.bands import MixedSourceError, UnknownLocationError, build_bands
from hargaikan.pipeline.cleaning import (
    CleanRow,
    IngestReport,
    ParsedPremise,
    clean,
    map_species,
    to_price_records,
    with_unmatched,
)
from hargaikan.pipeline.locations import build_hierarchy, by_id
from hargaikan.pipeline.publish import Batch, PublishReport, make_batch, publish, validate_batch
from hargaikan.pipeline.sources import (
    SourceFormatError,
    find_coverage_file,
    find_snapshot_files,
    read_coverage,
    read_price_snapshots,
)
from hargaikan.pipeline.synthetic import SYNTHETIC_SOURCE, generate

TARGETS = ("development", "test", "production")
DEFAULT_RAW_DIR = Path("data/raw")
DEFAULT_GEOCODE_CACHE = Path("data/geocode_cache.csv")
DEFAULT_SYNTHETIC_WEEKS = 16
DEFAULT_SYNTHETIC_SEED = 42
# Kept without synthetic data so the location fallback message can be seen in the demo.
SYNTHETIC_EMPTY_LOCATIONS = ("Pasar Chow Kit",)
# Species names used for --synthetic when data/raw has no snapshot files. Names only; the
# synthetic generator supplies the (demo-labelled) prices.
DEMO_SPECIES_NAMES = (
    "IKAN KEMBUNG",
    "IKAN SELAR",
    "IKAN TONGKOL",
    "IKAN BAWAL HITAM",
    "IKAN BAWAL PUTIH",
    "IKAN MERAH",
    "IKAN JENAHAK",
    "IKAN SIAKAP",
    "IKAN TENGGIRI",
    "IKAN KERISI",
)

EXIT_OK, EXIT_FAILURE, EXIT_USAGE = 0, 1, 2


class IngestError(RuntimeError):
    """The pipeline cannot produce a batch (no input files, bad source format, mixed sources)."""


@dataclass(frozen=True)
class IngestResult:
    target: str
    synthetic: bool
    dry_run: bool
    ingest_report: IngestReport | None  # None for --synthetic without raw files
    batch: Batch
    publish_report: PublishReport
    species_source: str  # "raw" or "demo"
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        return not self.publish_report.errors


# --- Inputs ---------------------------------------------------------------------------------------


def read_geocode_cache(path: str | Path | None) -> dict[str, dict[str, Any]]:
    """Minimal reader for data/geocode_cache.csv: premise_key → {lat, lng, status}.

    TODO(task 5.4): replace with ``hargaikan.pipeline.geocode.load_cache`` once the geocoder lands.
    Missing file → {}. Rows without a premise_key are skipped; build_hierarchy decides which
    coordinates are usable (status ok or manual only).
    """
    if path is None:
        return {}
    p = Path(path)
    if not p.is_file():
        return {}
    out: dict[str, dict[str, Any]] = {}
    with p.open(encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            key = (row.get("premise_key") or "").strip()
            if key:
                out[key] = {"lat": row.get("lat"), "lng": row.get("lng"), "status": (row.get("status") or "").strip()}
    return out


def _demo_rows(names: Sequence[str]) -> list[CleanRow]:
    """Name-only CleanRows so demo species go through map_species (same ids, catalog matching)."""
    placeholder = ParsedPremise(key="", chain="", branch=None, display="")
    return [
        CleanRow(
            species_name=n, grade=None, premise=placeholder, raw_item_name=n, price_rm_per_kg=None,
            price_low=None, price_high=None, price_min=None, price_max=None, record_count=None,
            date_start=None, date_end=None, snapshot=0, quality_status="ok",
        )
        for n in names
    ]


def _monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def default_synthetic_start(weeks: int, today: date | None = None) -> date:
    """Monday ``weeks`` weeks before the current week, so demo data ends last week."""
    return _monday(today or date.today()) - timedelta(weeks=weeks)


# --- Pipeline -------------------------------------------------------------------------------------


def run_ingest(
    raw_dir: str | Path,
    target: str,
    settings: Settings,
    *,
    repo: Any = None,
    catalog_species: Sequence[Species] = (),
    catalog_aliases: Sequence[SpeciesAlias] = (),
    synthetic: bool = False,
    dry_run: bool = False,
    geocode_cache: str | Path | None = DEFAULT_GEOCODE_CACHE,
    synthetic_weeks: int = DEFAULT_SYNTHETIC_WEEKS,
    synthetic_seed: int = DEFAULT_SYNTHETIC_SEED,
    synthetic_start: date | None = None,
) -> IngestResult:
    """Run the ingest pipeline and publish (or, with ``dry_run``, only validate) the batch.

    ``repo`` is a PublishRepository (required unless ``dry_run``). ``catalog_species`` and
    ``catalog_aliases`` are the existing Supabase catalog used by map_species. Raises IngestError
    when no batch can be built; publish problems are returned in ``publish_report.errors``.
    """
    if not dry_run and repo is None:
        raise ValueError("run_ingest needs a PublishRepository unless dry_run is set")
    files = find_snapshot_files(raw_dir)
    if not files and not synthetic:
        raise IngestError(f"no snapshot files matching ikansight_price_per_kg_by_location*.csv in {raw_dir}")

    notes: list[str] = []
    report: IngestReport | None = None
    rows_with_ids: list[tuple[CleanRow, str]] = []
    new_species: list[Species] = []
    new_aliases: list[SpeciesAlias] = []
    if files:
        try:
            raw_rows = read_price_snapshots(files)
            coverage_file = find_coverage_file(raw_dir)
            coverage = read_coverage(coverage_file) if coverage_file else None
        except SourceFormatError as exc:
            raise IngestError(str(exc)) from exc
        clean_rows, report = clean(raw_rows, settings, coverage)
        rows_with_ids, new_species, new_aliases = map_species(clean_rows, catalog_species, catalog_aliases)
        report = with_unmatched(report, new_species)
        notes.append(f"read {len(files)} snapshot file(s)")

    premises = [row.premise for row, _ in rows_with_ids]
    locations = build_hierarchy(premises, read_geocode_cache(geocode_cache))
    locations_by_id = by_id(locations)

    if synthetic:
        species_source = "raw" if files else "demo"
        if not files:
            rows_with_ids, new_species, new_aliases = map_species(
                _demo_rows(DEMO_SPECIES_NAMES), catalog_species, catalog_aliases
            )
        all_species = {s.id: s for s in (*catalog_species, *new_species)}
        species = [all_species[sid] for sid in sorted({sid for _, sid in rows_with_ids})]
        targets = [
            loc for loc in locations if loc.level in ("pasar", "premis") and loc.name not in SYNTHETIC_EMPTY_LOCATIONS
        ]
        start = synthetic_start or default_synthetic_start(synthetic_weeks)
        records: list[PriceRecord] = generate(species, targets, synthetic_weeks, synthetic_seed, start)
        notes.append(
            f"{SYNTHETIC_SOURCE}: {len(species)} species × {len(targets)} locations × {synthetic_weeks} weeks "
            f"from {start.isoformat()} (seed {synthetic_seed}); data demo only"
        )
        report = None
    else:
        species_source = "raw"
        records = to_price_records(rows_with_ids)

    try:
        bands = build_bands(records, locations_by_id, settings)
    except (MixedSourceError, UnknownLocationError) as exc:
        raise IngestError(str(exc)) from exc

    batch = make_batch(
        "prices", species=new_species, aliases=new_aliases, locations=locations, records=records, bands=bands
    )
    if dry_run:
        known_species = {s.id for s in catalog_species}
        errors = validate_batch(batch, target, known_species, set())
        publish_report = PublishReport(batch.batch_id, False, False, tuple(errors))
    else:
        publish_report = publish(batch, repo, target)
    return IngestResult(
        target=target,
        synthetic=synthetic,
        dry_run=dry_run,
        ingest_report=report,
        batch=batch,
        publish_report=publish_report,
        species_source=species_source,
        notes=tuple(notes),
    )


# --- Report ---------------------------------------------------------------------------------------


def _list(items: Sequence[str], limit: int = 20) -> str:
    shown = ", ".join(items[:limit])
    return shown + (f", … (+{len(items) - limit})" if len(items) > limit else "")


def format_report(result: IngestResult) -> str:
    mode = SYNTHETIC_SOURCE if result.synthetic else "IKANSIGHT"
    lines = [f"ingest: target={result.target} mode={mode}{' dry-run' if result.dry_run else ''}"]
    lines += [f"  note: {n}" for n in result.notes]
    r = result.ingest_report
    if r is not None:
        lines.append(
            f"  rows: in={r.rows_in} out={r.rows_out} duplicates={r.duplicates} conflicts={r.conflicts} "
            f"unrepresentable={r.unrepresentable}"
        )
        lines.append("  by status: " + ", ".join(f"{k}={v}" for k, v in r.by_status.items()))
        lines.append(f"  unmatched species ({len(r.unmatched_species)}): {_list(r.unmatched_species) or '-'}")
        lines.append(
            f"  coverage without prices ({len(r.coverage_without_prices)}): {_list(r.coverage_without_prices) or '-'}"
        )
    b = result.batch
    levels: dict[str, int] = {}
    for loc in b.locations:
        levels[loc.level] = levels.get(loc.level, 0) + 1
    located = sum(1 for loc in b.locations if loc.lat is not None and loc.lng is not None)
    lines.append(f"  batch {b.batch_id} (kind={b.kind})")
    lines.append(
        f"    new species={len(b.species)} new aliases={len(b.aliases)} locations={len(b.locations)} "
        f"({', '.join(f'{k}={v}' for k, v in levels.items())}; with coordinates={located})"
    )
    lines.append(f"    price_records={len(b.records)} price_bands={len(b.bands)}")
    p = result.publish_report
    if p.errors:
        lines.append(f"  result: FAILED ({len(p.errors)} error(s))")
        lines += [f"    - {e}" for e in p.errors[:50]]
        if len(p.errors) > 50:
            lines.append(f"    … (+{len(p.errors) - 50} more)")
    elif result.dry_run:
        lines.append("  result: dry run, validation passed; nothing written to Supabase")
    elif p.already_live:
        lines.append("  result: batch already live; nothing changed")
    else:
        lines.append("  result: published (staged, validated and promoted)")
    return "\n".join(lines)


# --- CLI ------------------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ingest.py", description="Clean IkanSight snapshots, build price bands and publish them to Supabase."
    )
    parser.add_argument("--raw", type=Path, default=DEFAULT_RAW_DIR, help="folder with snapshot CSVs (default data/raw)")
    parser.add_argument("--target", required=True, choices=TARGETS, help="must equal APP_ENV")
    parser.add_argument("--synthetic", action="store_true", help="publish SYNTHETIC demo records (development/test only)")
    parser.add_argument("--dry-run", action="store_true", help="validate and print the report; no Supabase writes")
    parser.add_argument(
        "--geocode-cache", type=Path, default=DEFAULT_GEOCODE_CACHE,
        help="geocode cache CSV read for premise coordinates if it exists (default data/geocode_cache.csv)",
    )
    # TODO(task 5.4): add --geocode (Nominatim lookups for premises missing from the cache).
    parser.add_argument("--synthetic-weeks", type=int, default=DEFAULT_SYNTHETIC_WEEKS)
    parser.add_argument("--synthetic-seed", type=int, default=DEFAULT_SYNTHETIC_SEED)
    parser.add_argument("--synthetic-start", type=date.fromisoformat, default=None, help="YYYY-MM-DD (default: weeks ago)")
    return parser


def load_dotenv_if_present(path: Path = Path(".env")) -> None:
    """Load KEY=VALUE pairs from .env without overriding variables already set."""
    if not path.is_file():
        return
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(path, override=False)


def main(
    argv: Sequence[str] | None = None,
    *,
    settings: Settings | None = None,
    repo: Any = None,
    out: TextIO | None = None,
    err: TextIO | None = None,
) -> int:
    """CLI entry point. ``settings``/``repo`` can be injected (tests); otherwise .env is loaded,
    settings are read from secrets/environment and a service-role Supabase repository is built."""
    out = out or sys.stdout
    err = err or sys.stderr
    args = build_parser().parse_args(list(argv) if argv is not None else None)

    if settings is None:
        load_dotenv_if_present()
        try:
            settings = load_settings()
        except ValueError as exc:  # message names the setting, never a value
            print(f"ingest: {exc}", file=err)
            return EXIT_USAGE
    if args.target != settings.app_env:
        print(f"ingest: --target {args.target} does not match APP_ENV={settings.app_env}; refusing to run", file=err)
        return EXIT_USAGE
    if args.synthetic and args.target == "production":
        print("ingest: --synthetic is for development and test only", file=err)
        return EXIT_USAGE
    if args.synthetic_weeks < 1:
        print("ingest: --synthetic-weeks must be at least 1", file=err)
        return EXIT_USAGE

    catalog_species: list[Species] = []
    catalog_aliases: list[SpeciesAlias] = []
    if not args.dry_run:
        if repo is None:
            from hargaikan.repositories.publish_repo import SupabasePublishRepository
            from hargaikan.repositories.supabase_client import MissingSecretError, safe_error, service_role_client

            try:
                repo = SupabasePublishRepository(service_role_client(settings))
            except MissingSecretError as exc:
                print(f"ingest: {exc}", file=err)
                return EXIT_USAGE
            try:
                catalog_species, catalog_aliases = repo.list_species(), repo.list_aliases()
            except Exception as exc:
                print(f"ingest: could not read the species catalog ({safe_error(exc)})", file=err)
                return EXIT_FAILURE
        elif hasattr(repo, "list_species") and hasattr(repo, "list_aliases"):
            catalog_species, catalog_aliases = list(repo.list_species()), list(repo.list_aliases())

    try:
        result = run_ingest(
            args.raw,
            args.target,
            settings,
            repo=repo,
            catalog_species=catalog_species,
            catalog_aliases=catalog_aliases,
            synthetic=args.synthetic,
            dry_run=args.dry_run,
            geocode_cache=args.geocode_cache,
            synthetic_weeks=args.synthetic_weeks,
            synthetic_seed=args.synthetic_seed,
            synthetic_start=args.synthetic_start,
        )
    except IngestError as exc:
        print(f"ingest: {exc}", file=err)
        return EXIT_FAILURE
    print(format_report(result), file=out)
    return EXIT_OK if result.ok else EXIT_FAILURE


__all__ = [
    "DEMO_SPECIES_NAMES",
    "IngestError",
    "IngestResult",
    "build_parser",
    "format_report",
    "main",
    "read_geocode_cache",
    "run_ingest",
]
