"""Location hierarchy seeding (Requirement 6.1, 6.2).

Pure logic: no I/O and no supabase, pinecone, requests or streamlit imports. Geocode entries are
passed in by the caller (``scripts/ingest.py`` / ``01_ingest.ipynb``); this module only decides
which coordinates may be used.

Hierarchy (v2): Malaysia (nasional) → Kuala Lumpur (negeri) → the 8 pasar and one premis per
supermarket branch. The kawasan level exists in the schema but is not populated in v2.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from hargaikan.domain.ids import location_id
from hargaikan.domain.models import Location
from hargaikan.pipeline.cleaning import ParsedPremise, default_location_id

NASIONAL_NAME = "Malaysia"
NEGERI_NAME = "Kuala Lumpur"
PASAR_NAMES = (
    "Pasar Chow Kit",
    "Pasar Datuk Keramat",
    "Pasar Jalan Raja Bot",
    "Pasar TTDI",
    "Pasar Sentul",
    "Pasar Pudu",
    "Pasar Selayang",
    "Pasar Besar Kuala Lumpur",
)
# Only these geocode statuses carry a usable coordinate (Requirement 9.6, 9.7).
USABLE_GEOCODE_STATUSES = frozenset({"ok", "manual"})

NASIONAL_ID = location_id("nasional", NASIONAL_NAME)
NEGERI_ID = location_id("negeri", NEGERI_NAME)


@dataclass(frozen=True)
class PremiseLocation(Location):
    """A premis Location that also carries the supermarket chain and branch (locations.chain/branch)."""

    chain: str | None = None
    branch: str | None = None


def _field(entry: Any, name: str) -> Any:
    """Read ``name`` from a geocode entry given as an object (GeocodeEntry) or a mapping (CSV row)."""
    if isinstance(entry, Mapping):
        return entry.get(name)
    return getattr(entry, name, None)


def _to_coordinate(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def coordinates_for(key: str, geocodes: Mapping[str, Any]) -> tuple[float | None, float | None]:
    """(lat, lng) from the geocode entry for ``key`` if its status is ok or manual and both values
    are finite numbers; otherwise (None, None). Coordinates are never estimated."""
    entry = geocodes.get(key)
    if entry is None:
        return None, None
    status = str(_field(entry, "status") or "").strip().lower()
    if status not in USABLE_GEOCODE_STATUSES:
        return None, None
    lat, lng = _to_coordinate(_field(entry, "lat")), _to_coordinate(_field(entry, "lng"))
    if lat is None or lng is None:
        return None, None
    return lat, lng


def unique_premises(premises: Iterable[ParsedPremise]) -> list[ParsedPremise]:
    """One premise per key (the first seen), sorted by key."""
    by_key: dict[str, ParsedPremise] = {}
    for p in premises:
        if p.key and p.key not in by_key:
            by_key[p.key] = p
    return [by_key[k] for k in sorted(by_key)]


def build_hierarchy(premises: Sequence[ParsedPremise], geocodes: Mapping[str, Any]) -> list[Location]:
    """Malaysia (nasional) → Kuala Lumpur (negeri) → 8 pasar + one premis per supermarket branch.

    Ids are deterministic (``domain.ids.location_id``); a premis id equals
    ``cleaning.default_location_id(premise)`` so price records link to it. ``geocodes`` maps a
    premise key (or a pasar name) to an entry with ``lat``, ``lng`` and ``status`` attributes or
    keys; lat/lng come only from entries with status ok or manual, otherwise they are None.
    Premis locations are :class:`PremiseLocation` values carrying chain and branch. The output is
    ordered parents first: nasional, negeri, pasar (in PASAR_NAMES order), premis (by key).
    """
    out: list[Location] = [
        Location(id=NASIONAL_ID, name=NASIONAL_NAME, level="nasional", parent_id=None, lat=None, lng=None),
        Location(id=NEGERI_ID, name=NEGERI_NAME, level="negeri", parent_id=NASIONAL_ID, lat=None, lng=None),
    ]
    for name in PASAR_NAMES:
        lat, lng = coordinates_for(name, geocodes)
        out.append(
            Location(id=location_id("pasar", name), name=name, level="pasar", parent_id=NEGERI_ID, lat=lat, lng=lng)
        )
    for p in unique_premises(premises):
        lat, lng = coordinates_for(p.key, geocodes)
        out.append(
            PremiseLocation(
                id=default_location_id(p),
                name=p.key,
                level="premis",
                parent_id=NEGERI_ID,
                lat=lat,
                lng=lng,
                chain=p.chain,
                branch=p.branch,
            )
        )
    return out


def by_id(locations: Iterable[Location]) -> dict[str, Location]:
    """Index locations by id (the Mapping shape used by bands and the location resolver)."""
    return {loc.id: loc for loc in locations}
