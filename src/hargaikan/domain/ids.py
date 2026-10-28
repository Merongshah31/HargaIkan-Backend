"""Deterministic identifiers (uuid5) for species and locations.

Pure data helpers: no I/O and no supabase, pinecone, requests or streamlit imports.
The same input always yields the same id, so re-running the pipeline never duplicates rows.
"""

from __future__ import annotations

import uuid

from hargaikan.domain.models import LocationLevel

# Fixed project namespace. Never change it: every stored species and location id derives from it.
NAMESPACE = uuid.UUID("6f1c2b8e-4a57-5d0e-9b3a-2f8e7c1d4a90")


def species_id(code: str) -> str:
    """uuid5(NAMESPACE, code) as a string. ``code`` is a species code such as "IKAN_BAWAL_HITAM"."""
    return str(uuid.uuid5(NAMESPACE, code))


def location_id(level: LocationLevel, name: str) -> str:
    """uuid5 of "location:{level}:{name}". Callers pass the canonical name (e.g. a premise key).

    The "location:" prefix (lower case, with colons) can never equal a species code, which only
    contains A–Z, 0–9 and "_", so species and location ids never collide.
    """
    return str(uuid.uuid5(NAMESPACE, f"location:{level}:{name}"))
