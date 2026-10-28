"""Tests for the ingest orchestration (hargaikan.pipeline.ingest), the CLI and the notebook wrapper."""

from __future__ import annotations

import io
import json
from datetime import date
from pathlib import Path

from hargaikan.config import Settings
from hargaikan.pipeline.ingest import DEMO_SPECIES_NAMES, main, read_geocode_cache, run_ingest
from hargaikan.pipeline.synthetic import SYNTHETIC_SOURCE
from tests.fakes import FakePublishRepository, batch_facts

ROOT = Path(__file__).resolve().parents[1]
HEADER = "item,premise,harga_RM_per_kg,harga_terendah,harga_tertinggi,bilangan_rekod,tarikh_mula,tarikh_akhir,harga_min,harga_maks\n"
ROWS = (
    "IKAN KEMBUNG,AEON ( AU2 ),12.50,11.00,14.00,3,12/10/2026,18/10/2026,,\n"
    "IKAN KEMBUNG (BESAR),NSK GROCER ( TIMES SQUARE ),15.00,,,2,2026-10-12,2026-10-18,,\n"
    "IKAN SELAR,AEON (AU2),9.90,,,1,12/10/2026,18/10/2026,,\n"
    "IKAN SELAR,AEON (AU2),-1,,,1,19/10/2026,25/10/2026,,\n"
)


def _raw(tmp_path: Path, rows: str = ROWS) -> Path:
    raw = tmp_path / "raw"
    raw.mkdir(parents=True)
    (raw / "ikansight_price_per_kg_by_location.csv").write_text(HEADER + rows, encoding="utf-8")
    # A "(1)" copy repeating one row: deduplicated, counted as a duplicate.
    (raw / "ikansight_price_per_kg_by_location (1).csv").write_text(HEADER + rows.splitlines(True)[0], encoding="utf-8")
    return raw


def test_dry_run_on_tiny_csv(tmp_path: Path) -> None:
    result = run_ingest(_raw(tmp_path), "test", Settings(app_env="test"), dry_run=True, geocode_cache=None)
    assert result.ok and result.dry_run and not result.publish_report.published
    r = result.ingest_report
    assert r is not None
    assert (r.rows_in, r.rows_out, r.duplicates) == (5, 4, 1)
    assert r.by_status["invalid_price"] == 1 and r.unrepresentable == 1
    assert r.unmatched_species == ("IKAN KEMBUNG", "IKAN SELAR")
    b = result.batch
    assert len(b.records) == 3 and {x.source_name for x in b.records} == {"IKANSIGHT"}
    assert sorted(loc.name for loc in b.locations if loc.level == "premis") == ["AEON (AU2)", "NSK GROCER (TIMES SQUARE)"]
    assert any(band.grade == "BESAR" for band in b.bands)
    assert {band.location_id for band in b.bands} >= {loc.id for loc in b.locations if loc.level in ("negeri", "nasional")}
    # Same input → same batch id (idempotent re-runs).
    again = run_ingest(_raw(tmp_path / "again"), "test", Settings(app_env="test"), dry_run=True, geocode_cache=None)
    assert again.batch.batch_id == b.batch_id


def test_publish_through_fake_repo_is_idempotent(tmp_path: Path) -> None:
    raw, repo = _raw(tmp_path), FakePublishRepository()
    first = run_ingest(raw, "development", Settings(), repo=repo, geocode_cache=None)
    assert first.publish_report.published and not first.publish_report.already_live
    assert repo.live_state() == batch_facts(first.batch)
    second = run_ingest(raw, "development", Settings(), repo=repo, geocode_cache=None)
    assert second.publish_report.already_live


def test_geocode_cache_coordinates(tmp_path: Path) -> None:
    cache = tmp_path / "geocode_cache.csv"
    cache.write_text(
        "premise_key,query,lat,lng,display_name,status,fetched_at\n"
        "AEON (AU2),\"AEON AU2, Kuala Lumpur, Malaysia\",3.2101,101.7552,AEON AU2,ok,2026-10-20T10:00:00\n"
        "NSK GROCER (TIMES SQUARE),q,,,,not_found,2026-10-20T10:00:01\n",
        encoding="utf-8",
    )
    assert read_geocode_cache(tmp_path / "missing.csv") == {}
    result = run_ingest(_raw(tmp_path), "test", Settings(app_env="test"), dry_run=True, geocode_cache=cache)
    coords = {loc.name: (loc.lat, loc.lng) for loc in result.batch.locations}
    assert coords["AEON (AU2)"] == (3.2101, 101.7552)
    assert coords["NSK GROCER (TIMES SQUARE)"] == (None, None)


def test_synthetic_dry_run_uses_demo_species(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    result = run_ingest(empty, "development", Settings(), synthetic=True, dry_run=True, geocode_cache=None,
                        synthetic_weeks=3, synthetic_start=date(2026, 9, 7))
    assert result.ok and result.species_source == "demo" and result.ingest_report is None
    b = result.batch
    assert sorted(s.canonical_name_ms for s in b.species) == sorted(DEMO_SPECIES_NAMES)
    assert {r.source_name for r in b.records} == {SYNTHETIC_SOURCE}
    assert all(band.band_source == SYNTHETIC_SOURCE for band in b.bands)
    chow_kit = next(loc for loc in b.locations if loc.name == "Pasar Chow Kit")
    assert all(band.location_id != chow_kit.id for band in b.bands)  # left empty to show the fallback
    assert len(b.records) == len(DEMO_SPECIES_NAMES) * 7 * 3


def test_cli_target_mismatch_exits_2(tmp_path: Path) -> None:
    err = io.StringIO()
    code = main(["--raw", str(_raw(tmp_path)), "--target", "production", "--dry-run"],
                settings=Settings(app_env="development"), out=io.StringIO(), err=err)
    assert code == 2
    assert "does not match APP_ENV" in err.getvalue()


def test_cli_refuses_synthetic_for_production(tmp_path: Path) -> None:
    err = io.StringIO()
    code = main(["--synthetic", "--target", "production"], settings=Settings(app_env="production"),
                out=io.StringIO(), err=err)
    assert code == 2 and "development and test only" in err.getvalue()


def test_cli_dry_run_prints_report_and_exits_0(tmp_path: Path) -> None:
    out = io.StringIO()
    code = main(["--raw", str(_raw(tmp_path)), "--target", "test", "--dry-run", "--geocode-cache", str(tmp_path / "none.csv")],
                settings=Settings(app_env="test"), out=out, err=io.StringIO())
    assert code == 0
    text = out.getvalue()
    assert "rows: in=5 out=4 duplicates=1" in text and "dry run, validation passed" in text


def test_cli_missing_raw_files_exits_1(tmp_path: Path) -> None:
    err = io.StringIO()
    code = main(["--raw", str(tmp_path), "--target", "test", "--dry-run"], settings=Settings(app_env="test"),
                out=io.StringIO(), err=err)
    assert code == 1 and "no snapshot files" in err.getvalue()


def test_cli_publish_failure_exits_1(tmp_path: Path) -> None:
    repo = FakePublishRepository(fail_on=["promote"])
    out = io.StringIO()
    code = main(["--raw", str(_raw(tmp_path)), "--target", "test"], settings=Settings(app_env="test"), repo=repo,
                out=out, err=io.StringIO())
    assert code == 1 and "publish_failed:InjectedFailure" in out.getvalue()


def test_cli_missing_service_role_secret_exits_2(tmp_path: Path) -> None:
    err = io.StringIO()
    code = main(["--raw", str(_raw(tmp_path)), "--target", "test"], settings=Settings(app_env="test"),
                out=io.StringIO(), err=err)
    assert code == 2 and "SUPABASE_URL" in err.getvalue() and "SUPABASE_SERVICE_ROLE_KEY" in err.getvalue()


def test_notebook_is_valid_and_calls_the_package() -> None:
    nb = json.loads((ROOT / "notebooks" / "01_ingest.ipynb").read_text(encoding="utf-8"))
    assert nb["nbformat"] == 4 and nb["nbformat_minor"] >= 4
    ids = [c["id"] for c in nb["cells"]]
    assert len(ids) == len(set(ids))
    for cell in nb["cells"]:
        assert cell["cell_type"] in ("markdown", "code") and isinstance(cell["source"], list)
        if cell["cell_type"] == "code":
            assert cell["outputs"] == [] and cell["execution_count"] is None
    code = "".join("".join(c["source"]) for c in nb["cells"] if c["cell_type"] == "code")
    assert "from hargaikan.pipeline.ingest import main" in code
    for forbidden in ("def ", "class ", "build_bands", "clean("):
        assert forbidden not in code
