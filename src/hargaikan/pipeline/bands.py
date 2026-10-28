"""Price band builder (Requirement 5; ok records only per Requirement 3.14).

Pure logic (layer 1): no I/O and no supabase, pinecone, requests or streamlit imports.

A band is computed for every (species, location, grade) group where location is the record's
location or any ancestor and grade is None (all grades) or the record's grade. Quantiles are
weighted by ``record_count`` (bilangan_rekod).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence

from hargaikan.config import Settings
from hargaikan.core.location_fallback import ancestors
from hargaikan.domain.models import ConfidenceLevel, Location, PriceBand, PriceRecord
from hargaikan.pipeline.synthetic import SYNTHETIC_SOURCE

GroupKey = tuple[str, str, str | None]  # (species_id, location_id, grade or None)


class MixedSourceError(ValueError):
    """A band group mixes SYNTHETIC and non-SYNTHETIC records (Requirement 5.6)."""

    def __init__(self, species_id: str, location_id: str, grade: str | None = None) -> None:
        self.species_id, self.location_id, self.grade = species_id, location_id, grade
        super().__init__(
            f"mixed_source: band for species {species_id} at location {location_id} "
            f"(grade {grade!r}) mixes {SYNTHETIC_SOURCE} and real records"
        )


class UnknownLocationError(ValueError):
    """A record references a location that is not in the hierarchy."""


def weighted_quantile(values: Sequence[float], weights: Sequence[int], q: float) -> float:
    """Inverse CDF of the multiset in which each value is repeated ``weight`` times.

    Returns the smallest value v such that the total weight of values ≤ v is at least
    ``q × Σweights``. Equal to the unweighted quantile of the expanded multiset, independent of
    input order. Raises ValueError for empty input, mismatched lengths, a weight < 1 or q outside [0, 1].
    """
    if len(values) != len(weights):
        raise ValueError("values and weights must have the same length")
    if not values:
        raise ValueError("weighted_quantile of an empty sequence")
    if not 0.0 <= q <= 1.0:
        raise ValueError("q must be in [0, 1]")
    if any(w < 1 for w in weights):
        raise ValueError("weights must be at least 1")
    pairs = sorted(zip(values, weights))
    target = q * sum(weights)
    cum = 0
    for v, w in pairs:
        cum += w
        if cum >= target:
            return v
    return pairs[-1][0]


def confidence_level(record_count: int, s: Settings) -> ConfidenceLevel:
    """rendah below ``confidence_sederhana_min``, sederhana below ``confidence_tinggi_min``, else tinggi."""
    if record_count < s.confidence_sederhana_min:
        return "rendah"
    if record_count < s.confidence_tinggi_min:
        return "sederhana"
    return "tinggi"


def _group_key_order(key: GroupKey) -> tuple[str, str, int, str]:
    sp, loc, grade = key
    return (sp, loc, 0 if grade is None else 1, grade or "")


def group_records(records: Sequence[PriceRecord], locations: Mapping[str, Location]) -> dict[GroupKey, list[PriceRecord]]:
    """ok records grouped by (species, location-or-ancestor, None | grade).

    Raises UnknownLocationError if an ok record's location is not in ``locations``.
    """
    groups: dict[GroupKey, list[PriceRecord]] = defaultdict(list)
    for r in records:
        if r.quality_status != "ok":
            continue
        own = locations.get(r.location_id)
        if own is None:
            raise UnknownLocationError(f"unknown_location: {r.location_id}")
        for loc in (own, *ancestors(r.location_id, locations)):
            groups[(r.species_id, loc.id, None)].append(r)
            if r.grade:
                groups[(r.species_id, loc.id, r.grade)].append(r)
    return dict(groups)


def build_bands(records: Sequence[PriceRecord], locations: Mapping[str, Location], s: Settings) -> list[PriceBand]:
    """Bands for every group from :func:`group_records`, sorted by (species, location, grade).

    p25/median/p75 are weighted quantiles of price_rm_per_kg with record_count weights;
    record_count is the weight sum; the period spans the earliest date_start to the latest
    date_end; band_source joins the sorted distinct source names with "+". Non-ok records are
    ignored. Raises MixedSourceError (and returns nothing) if any group mixes SYNTHETIC and real
    records; every group is checked before any band is built.
    """
    groups = group_records(records, locations)
    ordered = sorted(groups, key=_group_key_order)
    for key in ordered:
        sources = {r.source_name for r in groups[key]}
        if SYNTHETIC_SOURCE in sources and len(sources) > 1:
            raise MixedSourceError(*key)
    out: list[PriceBand] = []
    for key in ordered:
        rs = groups[key]
        sp, loc, grade = key
        v = [float(r.price_rm_per_kg) for r in rs]
        w = [int(r.record_count) for r in rs]
        n = sum(w)
        out.append(
            PriceBand(
                species_id=sp,
                location_id=loc,
                grade=grade,
                p25=weighted_quantile(v, w, 0.25),
                median=weighted_quantile(v, w, 0.5),
                p75=weighted_quantile(v, w, 0.75),
                record_count=n,
                confidence_level=confidence_level(n, s),
                period_start=min(r.date_start for r in rs),
                period_end=max(r.date_end for r in rs),
                band_source="+".join(sorted({r.source_name for r in rs})),
            )
        )
    return out
