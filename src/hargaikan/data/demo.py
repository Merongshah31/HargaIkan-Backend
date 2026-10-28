"""In-process demo dataset for ``DATA_BACKEND=memory`` (no credentials needed).

Built with exactly the functions ``scripts/ingest.py --synthetic`` uses when ``data/raw`` has no
snapshot files: the demo species names → ``map_species`` → ``build_hierarchy`` →
``synthetic.generate`` over the pasar locations (Pasar Chow Kit is left empty so the location
fallback can be seen) → ``build_bands``. Every price record and band is ``SYNTHETIC`` and must be
labelled "Data demo" wherever it is shown. Predictions are empty (nothing passes the forecast
gate on demo data).

Curated profiles and similarity edges come from ``data/curated/*.yaml`` when the files exist.
Entries for species codes outside the demo catalog are skipped with a warning; an invalid file
is skipped with a warning (the demo still starts). This module reads those files, so it is not
part of the pure layer.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from hargaikan.config import Settings
from hargaikan.domain.models import (
    FishProfile,
    Location,
    PriceBand,
    PricePrediction,
    PriceRecord,
    SimilarityEdge,
    Species,
    SpeciesAlias,
)
from hargaikan.pipeline.bands import build_bands
from hargaikan.pipeline.cleaning import map_species
from hargaikan.pipeline.curated import CuratedError, load_profiles, load_similarity
from hargaikan.pipeline.ingest import (
    DEFAULT_SYNTHETIC_SEED,
    DEFAULT_SYNTHETIC_WEEKS,
    DEMO_SPECIES_NAMES,
    SYNTHETIC_EMPTY_LOCATIONS,
    _demo_rows,
    default_synthetic_start,
    read_geocode_cache,
)
from hargaikan.pipeline.locations import build_hierarchy, by_id
from hargaikan.pipeline.synthetic import SYNTHETIC_SOURCE, generate

logger = logging.getLogger(__name__)

PROFILES_FILE = "fish_profiles.yaml"
SIMILARITY_FILE = "fish_similarity.yaml"
_REPO_ROOT = Path(__file__).resolve().parents[3]  # src/hargaikan/data/demo.py → repository root


def repo_path(path: str | Path) -> Path:
    """``path`` as given if it is absolute or exists relative to the working directory (the repo
    root on Render and locally), else relative to the repository root next to the package source
    (editable install, tests run from another directory)."""
    p = Path(path)
    if p.is_absolute() or p.exists():
        return p
    return _REPO_ROOT / p


def default_data_dir() -> Path:
    """The ``data`` folder (see :func:`repo_path`)."""
    return repo_path("data")


@dataclass(frozen=True)
class DemoDataset:
    species: tuple[Species, ...]
    aliases: tuple[SpeciesAlias, ...]
    locations: tuple[Location, ...]
    price_records: tuple[PriceRecord, ...]
    bands: tuple[PriceBand, ...]
    profiles: tuple[FishProfile, ...] = ()
    similarity: tuple[SimilarityEdge, ...] = ()
    predictions: tuple[PricePrediction, ...] = ()
    metrics: tuple[dict, ...] = ()
    start: date | None = None  # first synthetic week (Monday)
    weeks: int = 0
    seed: int = 0
    source: str = SYNTHETIC_SOURCE
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_demo(self) -> bool:
        """True when any band or record is SYNTHETIC (UI label "Data demo")."""
        return any(SYNTHETIC_SOURCE in b.band_source for b in self.bands) or any(
            r.source_name == SYNTHETIC_SOURCE for r in self.price_records
        )


# --- Curated files --------------------------------------------------------------------------------


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None


def _filter_known(text: str, code_fields: Sequence[str], known: set[str], label: str) -> tuple[str, list[str]]:
    """Drop entries whose code fields are not known species codes. Returns (YAML text, warnings).

    Anything that is not a list of mappings is passed through unchanged so the curated loader
    reports the problem.
    """
    try:
        data: Any = yaml.safe_load(text)
    except yaml.YAMLError:
        return text, []
    if not isinstance(data, list):
        return text, []
    kept, warnings = [], []
    for i, entry in enumerate(data):
        if isinstance(entry, dict):
            unknown = [str(entry.get(f)) for f in code_fields if isinstance(entry.get(f), str) and entry[f] not in known]
            if unknown:
                warnings.append(f"{label}: entry {i} skipped, unknown species code(s) {', '.join(unknown)}")
                continue
        kept.append(entry)
    return yaml.safe_dump(kept, allow_unicode=False, sort_keys=False), warnings


def load_curated(data_dir: Path, known_codes: set[str]) -> tuple[list[FishProfile], list[SimilarityEdge], list[str]]:
    """Profiles and similarity edges from ``data_dir/curated``; missing files give empty lists."""
    warnings: list[str] = []
    profiles: list[FishProfile] = []
    edges: list[SimilarityEdge] = []
    curated = data_dir / "curated"
    if (text := _read_text(curated / PROFILES_FILE)) is not None:
        text, w = _filter_known(text, ("species_code",), known_codes, PROFILES_FILE)
        warnings += w
        try:
            profiles = load_profiles(text, known_codes)
        except CuratedError as exc:
            warnings.append(f"{PROFILES_FILE}: not loaded ({len(exc.problems)} problem(s): {exc.problems[0]})")
    if (text := _read_text(curated / SIMILARITY_FILE)) is not None:
        text, w = _filter_known(text, ("from", "to"), known_codes, SIMILARITY_FILE)
        warnings += w
        try:
            edges = load_similarity(text, known_codes)
        except CuratedError as exc:
            warnings.append(f"{SIMILARITY_FILE}: not loaded ({len(exc.problems)} problem(s): {exc.problems[0]})")
    return profiles, edges, warnings


# --- Dataset --------------------------------------------------------------------------------------


def build_demo_dataset(
    today: date | None = None,
    weeks: int = DEFAULT_SYNTHETIC_WEEKS,
    seed: int = DEFAULT_SYNTHETIC_SEED,
    *,
    data_dir: Path | None = None,
    settings: Settings | None = None,
) -> DemoDataset:
    """The demo catalog, hierarchy, SYNTHETIC records and bands, plus curated drafts.

    Deterministic for the same ``today``, ``weeks``, ``seed`` and curated files: the synthetic
    weeks end in the week before ``today`` (``default_synthetic_start``). ``today`` defaults to
    ``date.today()``. ``data_dir`` holds ``curated/`` and ``geocode_cache.csv`` (default
    :func:`default_data_dir`). Species ``allergen_groups`` are taken from their curated profile.
    """
    if weeks < 1:
        raise ValueError("weeks must be at least 1")
    s = settings or Settings()
    data_dir = data_dir if data_dir is not None else default_data_dir()

    _, new_species, aliases = map_species(_demo_rows(DEMO_SPECIES_NAMES), (), ())
    # Same order as ingest --synthetic (sorted by id), so the same seed gives the same prices.
    species_list = sorted(new_species, key=lambda sp: sp.id)
    known_codes = {sp.code for sp in species_list}
    profiles, edges, warnings = load_curated(data_dir, known_codes)
    for w in warnings:
        logger.warning("demo dataset: %s", w)

    groups = {p.species_code: p.allergen_groups for p in profiles}
    species = tuple(replace(sp, allergen_groups=groups.get(sp.code, sp.allergen_groups)) for sp in species_list)

    locations = build_hierarchy([], read_geocode_cache(data_dir / "geocode_cache.csv"))
    targets = [loc for loc in locations if loc.level in ("pasar", "premis") and loc.name not in SYNTHETIC_EMPTY_LOCATIONS]
    start = default_synthetic_start(weeks, today)
    records = generate(species, targets, weeks, seed, start)
    bands = build_bands(records, by_id(locations), s)
    return DemoDataset(
        species=species,
        aliases=tuple(aliases),
        locations=tuple(locations),
        price_records=tuple(records),
        bands=tuple(bands),
        profiles=tuple(profiles),
        similarity=tuple(edges),
        start=start,
        weeks=weeks,
        seed=seed,
        warnings=tuple(warnings),
    )
