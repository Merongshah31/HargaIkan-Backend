"""Recommender: verified, data-backed, cheaper similar fish that respect the user's allergies.

Pure logic (layer 1): no I/O and no supabase, pinecone, requests, streamlit or fastapi imports.

Filter order (Requirement 16): allergy → verified edge → sufficient data → cheaper, then the
budget preference, ranking and the cap. Similarity comes only from curated edges and prices only
from resolved bands; nothing is estimated. The recommender never produces text saying a fish is
safe: candidates whose curated reason contains a Forbidden_Phrase are skipped and such difference
lines are dropped, so no result text can contain one.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date

from hargaikan.config import Settings
from hargaikan.core.location_fallback import BandResolution
from hargaikan.core.text import normalise_term
from hargaikan.domain import messages as m
from hargaikan.domain.models import (
    ConfidenceLevel,
    FishProfile,
    SimilarityEdge,
    Species,
    SpeciesAlias,
    UserPreferences,
)

CONF_RANK: dict[str, int] = {"rendah": 0, "sederhana": 1, "tinggi": 2}
SYNTHETIC = "SYNTHETIC"

# Profile fields compared by profile_differences, in display order.
_PROFILE_FIELDS = ("texture", "taste", "bone_level", "cooking_methods", "allergen_groups")


@dataclass(frozen=True)
class RecommendationItem:
    species: Species
    reason: str
    differences: tuple[str, ...]
    p25: float
    median: float
    p75: float
    savings_rm_per_kg: float
    record_count: int
    confidence_level: ConfidenceLevel
    location_name: str
    period_start: date
    period_end: date
    similarity_score: float
    over_budget: bool  # UI shows OVER_BUDGET ("Melebihi bajet") when true
    is_demo: bool  # UI shows DEMO_DATA ("Data demo") when true


@dataclass(frozen=True)
class RecommendationResult:
    items: tuple[RecommendationItem, ...]
    empty_message: str | None  # NO_RECOMMENDATION when items is empty
    empty_reason: str | None  # e.g. ORIGINAL_NO_DATA
    original_allergy_match: bool  # drives ALLERGY_CAUTION
    disclaimer: str  # always RECOMMENDATION_DISCLAIMER


def contains_forbidden(text: str) -> bool:
    """True if ``text`` contains any Forbidden_Phrase (case-insensitive)."""
    lowered = text.lower()
    return any(p.lower() in lowered for p in m.FORBIDDEN_PHRASES)


def matches_allergy(sp: Species, aliases: Sequence[SpeciesAlias], prefs: UserPreferences) -> bool:
    """True if ``sp`` matches the user's allergy species ids, free-text terms or allergen groups.

    Terms match as normalised substrings of the canonical names or any alias of ``sp`` (errs toward
    exclusion). Terms that normalise to an empty string are ignored. Uses only catalog data.
    """
    if sp.id in prefs.allergy_species_ids:
        return True
    if set(sp.allergen_groups) & set(prefs.allergy_groups):
        return True
    terms = {nt for t in prefs.allergy_terms if (nt := normalise_term(t))}
    if not terms:
        return False
    names = {normalise_term(sp.canonical_name_ms)}
    if sp.canonical_name_en:
        names.add(normalise_term(sp.canonical_name_en))
    names |= {normalise_term(a.alias) for a in aliases if a.species_id == sp.id}
    return any(t in n for t in terms for n in names)


def _display(value: object) -> str:
    if isinstance(value, tuple):
        return ", ".join(str(v) for v in sorted(value)) if value else m.PROFILE_EMPTY_VALUE
    text = str(value).strip() if value is not None else ""
    return text or m.PROFILE_EMPTY_VALUE


def _comparable(value: object) -> object:
    return frozenset(value) if isinstance(value, tuple) else value


def profile_differences(a: FishProfile | None, b: FishProfile | None) -> tuple[str, ...]:
    """Fields that differ between profile ``a`` (original) and ``b`` (candidate).

    e.g. ``("Tekstur: lembut → pejal",)``. Tuple fields are compared as sets. Returns
    ``(PROFILE_INCOMPLETE,)`` when either profile is missing and ``()`` when nothing differs.
    """
    if a is None or b is None:
        return (m.PROFILE_INCOMPLETE,)
    out = []
    for field in _PROFILE_FIELDS:
        va, vb = getattr(a, field), getattr(b, field)
        if _comparable(va) != _comparable(vb):
            out.append(
                m.PROFILE_DIFF_TEMPLATE.format(
                    field=m.PROFILE_FIELD_LABELS[field], a=_display(va), b=_display(vb)
                )
            )
    return tuple(out)


@dataclass(frozen=True)
class _Eligible:
    edge: SimilarityEdge
    cand: Species
    res: BandResolution
    savings: float


def _rank_key(x: _Eligible) -> tuple:
    assert x.res.band is not None
    return (-x.edge.similarity_score, -x.savings, -CONF_RANK[x.res.band.confidence_level], x.cand.code)


def _to_item(
    x: _Eligible, original: Species, profiles: Mapping[str, FishProfile], over_budget: bool
) -> RecommendationItem:
    band, used = x.res.band, x.res.used
    assert band is not None and used is not None
    diffs = profile_differences(profiles.get(original.code), profiles.get(x.cand.code))
    return RecommendationItem(
        species=x.cand,
        reason=x.edge.reason,
        differences=tuple(d for d in diffs if not contains_forbidden(d)),
        p25=band.p25,
        median=band.median,
        p75=band.p75,
        savings_rm_per_kg=x.savings,
        record_count=band.record_count,
        confidence_level=band.confidence_level,
        location_name=used.name,
        period_start=band.period_start,
        period_end=band.period_end,
        similarity_score=x.edge.similarity_score,
        over_budget=over_budget,
        is_demo=SYNTHETIC in band.band_source.upper(),
    )


def recommend(
    original: Species,
    prefs: UserPreferences | None,
    catalog: Mapping[str, Species],
    aliases: Sequence[SpeciesAlias],
    edges: Sequence[SimilarityEdge],
    profiles: Mapping[str, FishProfile],
    resolve: Callable[[str], BandResolution],
    s: Settings,
) -> RecommendationResult:
    """Cheaper verified similar fish for ``original``.

    ``catalog`` maps species id → Species, ``profiles`` maps species code → FishProfile and
    ``resolve(species_id)`` resolves a band at the same selected location as the original.
    """
    prefs = prefs or UserPreferences(user_id="")
    allergic_orig = matches_allergy(original, aliases, prefs)
    orig = resolve(original.id)
    if orig.band is None:
        return RecommendationResult((), m.NO_RECOMMENDATION, m.ORIGINAL_NO_DATA, allergic_orig, m.RECOMMENDATION_DISCLAIMER)

    best: dict[str, _Eligible] = {}  # one entry per candidate species (highest-ranked edge wins)
    for e in edges:
        if e.from_species_id != original.id or e.to_species_id == original.id:
            continue
        cand = catalog.get(e.to_species_id)
        if cand is None or not cand.active:
            continue
        if matches_allergy(cand, aliases, prefs):  # 1 allergy first
            continue
        if not e.verified:  # 2 verified only
            continue
        if not e.reason.strip() or contains_forbidden(e.reason):  # never show unsafe/empty text
            continue
        r = resolve(cand.id)
        if r.band is None or r.used is None or r.band.record_count < s.min_records_for_recommendation:
            continue  # 3 enough data
        savings = orig.band.median - r.band.median
        if savings <= 0:  # 4 strictly cheaper
            continue
        x = _Eligible(e, cand, r, savings)
        if cand.id not in best or _rank_key(x) < _rank_key(best[cand.id]):
            best[cand.id] = x
    eligible = list(best.values())
    if not eligible:
        return RecommendationResult((), m.NO_RECOMMENDATION, None, allergic_orig, m.RECOMMENDATION_DISCLAIMER)

    budget = prefs.budget_rm_per_kg
    within = [x for x in eligible if budget is None or x.res.band.median <= budget]  # 5 budget
    if within:
        chosen, over = sorted(within, key=_rank_key), False  # 6 rank
    else:
        assert budget is not None
        chosen = sorted(eligible, key=lambda x: (x.res.band.median - budget, _rank_key(x)))
        over = True
    items = tuple(_to_item(x, original, profiles, over) for x in chosen[: s.max_recommendations])  # 7 cap
    return RecommendationResult(items, None, None, allergic_orig, m.RECOMMENDATION_DISCLAIMER)
