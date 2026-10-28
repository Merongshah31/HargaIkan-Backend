"""Location hierarchy helpers and the band fallback resolver.

Pure logic (layer 1): no I/O and no supabase, pinecone, requests, streamlit or fastapi imports.

``ancestors`` is used by the band builder. ``resolve_band`` picks the nearest ancestor-or-self
band for a species (Requirement 6.3–6.6).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from hargaikan.domain import messages as m
from hargaikan.domain.models import Location, PriceBand


class UnknownLocationError(KeyError):
    """Raised by :func:`resolve_band` when ``location_id`` is not in the location mapping.

    ``BandResolution.requested`` must be a real :class:`Location`, so an unknown id cannot be
    represented as a resolution. Callers (services / API) should map this to a 404 or validation
    error; locations are only ever chosen from the listed ones.
    """


@dataclass(frozen=True)
class BandResolution:
    band: PriceBand | None
    requested: Location
    used: Location | None
    is_fallback: bool
    message: str | None  # FALLBACK_TEMPLATE text or NO_DATA


def ancestors(location_id: str, locations: Mapping[str, Location]) -> list[Location]:
    """Ancestors of ``location_id``, nearest first (parent, grandparent, …), excluding itself.

    Follows ``parent_id`` with a visited set, so it terminates even on bad data: the walk stops
    at a missing parent, an unknown id or the first location already seen (a cycle). An unknown
    ``location_id`` has no ancestors.
    """
    start = locations.get(location_id)
    if start is None:
        return []
    visited = {start.id}
    out: list[Location] = []
    parent_id = start.parent_id
    while parent_id is not None and parent_id not in visited:
        parent = locations.get(parent_id)
        if parent is None:
            break
        visited.add(parent.id)
        out.append(parent)
        parent_id = parent.parent_id
    return out


def resolve_band(
    location_id: str,
    species_id: str,
    grade: str | None,
    bands: Iterable[PriceBand],
    locations: Mapping[str, Location],
) -> BandResolution:
    """Nearest ancestor-or-self band for ``species_id`` at ``location_id``.

    At each level (the requested location first, then its ancestors nearest first) a band for the
    requested ``grade`` is preferred over the all-grade band (``grade is None``). A blank or None
    grade only matches all-grade bands. ``is_fallback`` is true exactly when the used location
    differs from the requested one, and then ``message`` is ``FALLBACK_TEMPLATE`` filled with both
    names. With no band anywhere on the chain, ``band`` is None and ``message`` is ``NO_DATA``.

    Raises UnknownLocationError if ``location_id`` is not in ``locations``.
    """
    try:
        requested = locations[location_id]
    except KeyError:
        raise UnknownLocationError(location_id) from None

    idx = {(b.location_id, b.grade): b for b in bands if b.species_id == species_id}
    # Loop invariant: no location visited before the current one has a usable band.
    for loc in [requested, *ancestors(location_id, locations)]:
        band = idx.get((loc.id, grade)) if grade else None
        if band is None:
            band = idx.get((loc.id, None))
        if band is not None:
            fallback = loc.id != requested.id
            message = m.FALLBACK_TEMPLATE.format(requested=requested.name, used=loc.name) if fallback else None
            return BandResolution(band, requested, loc, fallback, message)
    return BandResolution(None, requested, None, False, m.NO_DATA)
