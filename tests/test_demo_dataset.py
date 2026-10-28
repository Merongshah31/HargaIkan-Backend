"""Demo dataset for DATA_BACKEND=memory and the shipped curated YAML drafts."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from hargaikan.data.demo import build_demo_dataset, load_curated
from hargaikan.domain.ids import species_id
from hargaikan.pipeline.curated import load_profiles, load_similarity
from hargaikan.pipeline.ingest import DEMO_SPECIES_NAMES, SYNTHETIC_EMPTY_LOCATIONS
from hargaikan.pipeline.locations import NEGERI_ID
from hargaikan.pipeline.synthetic import SYNTHETIC_SOURCE

ROOT = Path(__file__).resolve().parents[1]
CURATED = ROOT / "data" / "curated"
TODAY = date(2025, 6, 18)
DEMO_CODES = {
    "IKAN_KEMBUNG", "IKAN_SELAR", "IKAN_TONGKOL", "IKAN_BAWAL_HITAM", "IKAN_BAWAL_PUTIH",
    "IKAN_MERAH", "IKAN_JENAHAK", "IKAN_SIAKAP", "IKAN_TENGGIRI", "IKAN_KERISI",
}


@pytest.fixture(scope="module")
def demo():
    return build_demo_dataset(TODAY)


def test_demo_dataset_is_deterministic(demo) -> None:
    again = build_demo_dataset(TODAY)
    assert again == demo
    assert build_demo_dataset(TODAY, seed=7).price_records != demo.price_records


def test_demo_species_are_the_ingest_demo_list(demo) -> None:
    assert {s.code for s in demo.species} == DEMO_CODES
    assert len(DEMO_SPECIES_NAMES) == len(demo.species)
    assert all(s.id == species_id(s.code) for s in demo.species)


def test_every_demo_record_and_band_is_synthetic(demo) -> None:
    assert demo.is_demo
    assert demo.price_records and all(r.source_name == SYNTHETIC_SOURCE for r in demo.price_records)
    assert all(b.band_source == SYNTHETIC_SOURCE for b in demo.bands)
    assert demo.predictions == ()


def test_demo_weeks_end_before_today(demo) -> None:
    assert demo.start == date(2025, 2, 24)
    assert max(r.date_end for r in demo.price_records) < TODAY
    assert {r.date_start for r in demo.price_records} == {
        date.fromordinal(demo.start.toordinal() + 7 * w) for w in range(demo.weeks)
    }


def test_kl_negeri_band_exists_for_every_species(demo) -> None:
    kl = {b.species_id for b in demo.bands if b.location_id == NEGERI_ID and b.grade is None}
    assert kl == {s.id for s in demo.species}


def test_empty_demo_location_has_no_data(demo) -> None:
    empty = {loc.id for loc in demo.locations if loc.name in SYNTHETIC_EMPTY_LOCATIONS}
    assert empty
    assert not any(r.location_id in empty for r in demo.price_records)
    assert not any(b.location_id in empty for b in demo.bands)


def test_curated_drafts_are_loaded_and_unverified(demo) -> None:
    assert {p.species_code for p in demo.profiles} == DEMO_CODES
    assert demo.similarity and not any(e.verified for e in demo.similarity)
    assert demo.warnings == ()
    groups = {s.code: s.allergen_groups for s in demo.species}
    assert all(g == ("ikan_bersirip",) for g in groups.values())


def test_shipped_curated_files_validate_strictly() -> None:
    profiles = load_profiles((CURATED / "fish_profiles.yaml").read_text(encoding="utf-8"), DEMO_CODES)
    edges = load_similarity((CURATED / "fish_similarity.yaml").read_text(encoding="utf-8"), DEMO_CODES)
    assert len(profiles) == 10
    assert all(p.allergen_groups == ("ikan_bersirip",) for p in profiles)
    assert edges and all(e.verified is False for e in edges)
    assert all(e.reason.strip() and e.basis for e in edges)


def test_every_shipped_similarity_entry_says_verified_false() -> None:
    import yaml

    data = yaml.safe_load((CURATED / "fish_similarity.yaml").read_text(encoding="utf-8"))
    assert data and all(entry.get("verified") is False for entry in data)


def test_unknown_codes_are_skipped_with_warning(tmp_path: Path) -> None:
    (tmp_path / "curated").mkdir()
    (tmp_path / "curated" / "fish_profiles.yaml").write_text(
        "- species_code: IKAN_KEMBUNG\n  texture: lembut\n  taste: manis\n  bone_level: sedikit\n"
        "  cooking_methods: [goreng]\n  allergen_groups: [ikan_bersirip]\n"
        "- species_code: IKAN_TIDAK_WUJUD\n  texture: lembut\n  taste: manis\n  bone_level: sedikit\n"
        "  cooking_methods: [goreng]\n  allergen_groups: [ikan_bersirip]\n",
        encoding="utf-8",
    )
    (tmp_path / "curated" / "fish_similarity.yaml").write_text(
        "- {from: IKAN_KEMBUNG, to: IKAN_TIDAK_WUJUD, similarity_score: 0.5, reason: x, basis: [saiz], verified: false}\n",
        encoding="utf-8",
    )
    profiles, edges, warnings = load_curated(tmp_path, DEMO_CODES)
    assert [p.species_code for p in profiles] == ["IKAN_KEMBUNG"]
    assert edges == []
    assert len(warnings) == 2 and all("IKAN_TIDAK_WUJUD" in w for w in warnings)


def test_invalid_curated_file_is_skipped_not_fatal(tmp_path: Path) -> None:
    (tmp_path / "curated").mkdir()
    (tmp_path / "curated" / "fish_similarity.yaml").write_text(
        "- {from: IKAN_KEMBUNG, to: IKAN_SELAR, similarity_score: 3, reason: x, basis: [saiz]}\n", encoding="utf-8"
    )
    d = build_demo_dataset(TODAY, data_dir=tmp_path)
    assert d.similarity == () and d.profiles == ()
    assert any("fish_similarity.yaml: not loaded" in w for w in d.warnings)
    assert d.bands  # prices are still available


def test_missing_curated_files_give_empty_lists(tmp_path: Path) -> None:
    assert load_curated(tmp_path, DEMO_CODES) == ([], [], [])


def test_weeks_must_be_positive() -> None:
    with pytest.raises(ValueError):
        build_demo_dataset(TODAY, weeks=0)
