"""Memory repositories (DATA_BACKEND=memory)."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timezone

import pytest

from hargaikan.config import Settings
from hargaikan.data.demo import DemoDataset, build_demo_dataset
from hargaikan.domain.models import (
    Location,
    PricePrediction,
    PriceRecord,
    Species,
    UserPreferences,
    UserProfile,
)
from hargaikan.pipeline.bands import build_bands
from hargaikan.pipeline.locations import NEGERI_ID
from hargaikan.repositories.memory_repo import (
    ConsentRequiredError,
    MemoryCatalogRepository,
    MemoryPredictionRepository,
    MemoryPriceRepository,
    MemoryUserRepository,
    MemoryUserStore,
)
from hargaikan.repositories.protocols import (
    CatalogRepository,
    PredictionRepository,
    PriceRepository,
    UserRepository,
)

SP, OTHER = "sp-1", "sp-2"


def _loc(id_: str, level: str, parent: str | None) -> Location:
    return Location(id=id_, name=id_, level=level, parent_id=parent, lat=None, lng=None)  # type: ignore[arg-type]


def _rec(sp: str, loc: str, price: float, count: int, start: date, status: str = "ok") -> PriceRecord:
    return PriceRecord(
        species_id=sp, location_id=loc, raw_item_name="IKAN X", grade=None, unit="kg", price_rm_per_kg=price,
        price_low=None, price_high=None, record_count=count, date_start=start,
        date_end=date.fromordinal(start.toordinal() + 6), source_name="IKANSIGHT", source_snapshot=1,
        quality_status=status,  # type: ignore[arg-type]
    )


W1, W2 = date(2025, 6, 2), date(2025, 6, 9)  # Mondays
LOCATIONS = (_loc("MY", "nasional", None), _loc("KL", "negeri", "MY"), _loc("P1", "pasar", "KL"), _loc("P2", "premis", "KL"))
RECORDS = (
    _rec(SP, "P2", 20.0, 2, W2),  # deliberately out of order
    _rec(SP, "P1", 10.0, 1, W1),
    _rec(SP, "P2", 14.0, 3, W1),
    _rec(SP, "P1", 12.0, 1, W2),
    _rec(SP, "P1", 999.0, 50, W2, status="outlier"),  # not ok: excluded
    _rec(OTHER, "P1", 50.0, 1, W1),  # other species: excluded
)


@pytest.fixture
def small() -> DemoDataset:
    locs = {loc.id: loc for loc in LOCATIONS}
    return DemoDataset(
        species=(Species(SP, "IKAN_X", "IKAN X", None, (), True), Species(OTHER, "IKAN_Y", "IKAN Y", None, (), False)),
        aliases=(),
        locations=LOCATIONS,
        price_records=RECORDS,
        bands=tuple(build_bands(RECORDS, locs, Settings())),
    )


def test_memory_repos_implement_protocols(small: DemoDataset) -> None:
    assert isinstance(MemoryCatalogRepository(small), CatalogRepository)
    assert isinstance(MemoryPriceRepository(small), PriceRepository)
    assert isinstance(MemoryPredictionRepository(small), PredictionRepository)
    assert isinstance(MemoryUserRepository(MemoryUserStore(), "u1"), UserRepository)


def test_weekly_series_ancestor_includes_descendants(small: DemoDataset) -> None:
    series = MemoryPriceRepository(small).weekly_series(SP, "KL")
    # W1: 10×1, 14×3 → median 14, n=4; W2: 12×1, 20×2 → median 20, n=3
    assert series == [(W1, 14.0, 4), (W2, 20.0, 3)]
    assert MemoryPriceRepository(small).weekly_series(SP, "MY") == series


def test_weekly_series_leaf_location_only(small: DemoDataset) -> None:
    repo = MemoryPriceRepository(small)
    assert repo.weekly_series(SP, "P1") == [(W1, 10.0, 1), (W2, 12.0, 1)]
    assert repo.weekly_series(SP, "P2") == [(W1, 14.0, 3), (W2, 20.0, 2)]
    assert repo.weekly_series(SP, "NOWHERE") == []


def test_weekly_series_sorted_and_counts_match_band(small: DemoDataset) -> None:
    repo = MemoryPriceRepository(small)
    series = repo.weekly_series(SP, "KL")
    assert [w for w, _, _ in series] == sorted(w for w, _, _ in series)
    band = next(b for b in repo.bands_for_species(SP) if b.location_id == "KL" and b.grade is None)
    assert sum(n for _, _, n in series) == band.record_count


def test_catalog_and_overview(small: DemoDataset) -> None:
    cat = MemoryCatalogRepository(small)
    assert [s.id for s in cat.list_species()] == [SP]
    assert len(cat.list_species(active_only=False)) == 2
    assert MemoryPriceRepository(small).catalog_overview() == {
        "species_count": 1, "premise_count": 2, "latest_period": date(2025, 6, 15), "demo": False,
    }


def test_demo_backend_bands_and_overview() -> None:
    d = build_demo_dataset(date(2025, 6, 18))
    prices = MemoryPriceRepository(d)
    for sp in d.species:
        assert any(b.location_id == NEGERI_ID for b in prices.bands_for_species(sp.id))
        series = prices.weekly_series(sp.id, NEGERI_ID)
        assert len(series) == d.weeks
    o = prices.catalog_overview()
    assert o["demo"] is True and o["species_count"] == 10 and o["premise_count"] == 8
    assert MemoryPredictionRepository(d).live_prediction(d.species[0].id, NEGERI_ID) is None
    assert MemoryPredictionRepository(d).metrics_for("any") == []
    edges = MemoryCatalogRepository(d).list_similarity(d.species[0].id)
    assert all(e.from_species_id == d.species[0].id for e in edges)


def test_prediction_repo_returns_latest_live(small: DemoDataset) -> None:
    def pred(start: date, status: str) -> PricePrediction:
        return PricePrediction(SP, "KL", start, start, 10.0, None, None, "baseline_median4", "v1", 1.0, start, start, status)  # type: ignore[arg-type]

    d = replace(small, predictions=(pred(W1, "live"), pred(W2, "live"), pred(date(2025, 7, 1), "staged")))
    assert MemoryPredictionRepository(d).live_prediction(SP, "KL").target_start == W2  # type: ignore[union-attr]


# --- users ----------------------------------------------------------------------------------------


def _profile(uid: str, allergies: bool = False, location: bool = False) -> UserProfile:
    now = datetime(2025, 6, 1, tzinfo=timezone.utc)
    return UserProfile(uid, allergies, location, now if allergies else None, now if location else None)


def test_user_repo_is_bound_to_user_id() -> None:
    store = MemoryUserStore()
    a, b = MemoryUserRepository(store, "a"), MemoryUserRepository(store, "b")
    a.upsert_profile(_profile("a"))
    a.upsert_preferences(UserPreferences("a", preferred_species_ids=(SP,), budget_rm_per_kg=30.0))
    assert a.get_profile() == _profile("a")
    assert b.get_profile() is None and b.get_preferences() is None
    with pytest.raises(PermissionError):
        b.upsert_profile(_profile("a"))


def test_user_repo_enforces_consent_like_the_database() -> None:
    repo = MemoryUserRepository(MemoryUserStore(), "a")
    with pytest.raises(ConsentRequiredError) as exc:
        repo.upsert_preferences(UserPreferences("a", allergy_terms=("udang",)))
    assert exc.value.code == "consent_allergies_required"
    with pytest.raises(ConsentRequiredError):
        repo.upsert_preferences(UserPreferences("a", preferred_location_id="KL"))
    repo.upsert_profile(_profile("a", allergies=True, location=True))
    repo.upsert_preferences(UserPreferences("a", allergy_terms=("udang",), preferred_location_id="KL"))
    assert repo.get_preferences().allergy_terms == ("udang",)  # type: ignore[union-attr]


def test_user_repo_delete_all_only_affects_own_rows() -> None:
    store = MemoryUserStore()
    a, b = MemoryUserRepository(store, "a"), MemoryUserRepository(store, "b")
    for r, uid in ((a, "a"), (b, "b")):
        r.upsert_profile(_profile(uid))
        r.upsert_preferences(UserPreferences(uid))
    a.delete_all()
    assert a.get_profile() is None and a.get_preferences() is None
    assert b.get_profile() is not None and b.get_preferences() is not None


def test_user_repo_requires_user_id() -> None:
    with pytest.raises(ValueError):
        MemoryUserRepository(MemoryUserStore(), "")
