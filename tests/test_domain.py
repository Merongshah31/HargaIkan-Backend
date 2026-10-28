"""Unit tests for hargaikan.domain (models and Bahasa Melayu messages)."""

from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError
from datetime import date
from pathlib import Path

import pytest

from hargaikan.domain import messages as m
from hargaikan.domain.models import PriceBand, SourceLabel, UserPreferences

DOMAIN_DIR = Path(__file__).resolve().parents[1] / "src" / "hargaikan" / "domain"
BANNED_IMPORTS = {"supabase", "pinecone", "requests", "streamlit"}


def test_source_labels_are_exact() -> None:
    assert [label.value for label in SourceLabel] == [
        "Harga sejarah",
        "Input pengguna",
        "Anggaran model",
        "Anggaran perjalanan",
    ]
    assert SourceLabel.HARGA_SEJARAH == "Harga sejarah"  # str enum compares to its text


def test_fallback_template_matches_srs_example() -> None:
    text = m.FALLBACK_TEMPLATE.format(requested="Pasar Chow Kit", used="Kuala Lumpur")
    assert text == "Data khusus Pasar Chow Kit belum tersedia. Rujukan Kuala Lumpur digunakan."


def test_fixed_srs_strings() -> None:
    assert m.VERDICT_BELOW == "Di bawah julat sejarah"
    assert m.VERDICT_WITHIN == "Dalam julat sejarah"
    assert m.VERDICT_ABOVE == "Di atas julat sejarah"
    assert m.NO_DATA == "Data belum tersedia"
    assert m.NO_RECOMMENDATION == "Tiada cadangan"
    assert m.TRAVEL_NOT_ESTIMATED == "Kos perjalanan belum dianggarkan"
    assert m.DEMO_DATA == "Data demo"
    assert m.RECOMMENDATION_DISCLAIMER == (
        "Cadangan ini bukan nasihat perubatan. Jika anda mempunyai alergi makanan, semak nama spesies, "
        "label produk dan risiko pencemaran silang dengan penjual atau profesional kesihatan."
    )


def test_templates_format() -> None:
    assert m.BUDGET_INVALID.format(max=1000) == "Bajet mesti nombor antara RM0.01 dan RM1000/kg."
    early = m.EARLY_BUY.format(p10="20.00", p90="28.50")
    assert "RM20.00–RM28.50/kg" in early
    assert "bukan arahan untuk membeli" in early and "peti sejuk" in early


def test_no_message_contains_a_forbidden_phrase() -> None:
    texts = [v for k, v in vars(m).items() if k.isupper() and isinstance(v, str)]
    assert texts
    for text in texts:
        for phrase in m.FORBIDDEN_PHRASES:
            assert phrase.lower() not in text.lower(), (text, phrase)


def test_models_are_frozen_with_design_defaults() -> None:
    prefs = UserPreferences(user_id="u1")
    assert prefs.preferred_species_ids == () and prefs.budget_rm_per_kg is None
    band = PriceBand("sp1", "kl", None, 20.0, 24.0, 30.0, 12, "tinggi", date(2026, 10, 12), date(2026, 10, 26), "IKANSIGHT")
    with pytest.raises(FrozenInstanceError):
        band.p25 = 1.0  # type: ignore[misc]


@pytest.mark.parametrize("path", sorted(DOMAIN_DIR.glob("*.py")), ids=lambda p: p.name)
def test_domain_has_no_io_package_imports(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert not imported & BANNED_IMPORTS
