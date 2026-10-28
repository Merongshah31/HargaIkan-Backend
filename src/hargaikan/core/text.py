"""Text normalisation shared by cleaning, catalog mapping, search and allergy matching.

Pure logic: no I/O and no supabase, pinecone, requests or streamlit imports.
"""

from __future__ import annotations

import unicodedata

# Unicode case mapping and NFKC do not always commute (e.g. "ΐ".upper() is not NFKC-stable and
# "ẞ".lower().upper() == "SS"), so each function iterates its single step to a fixed point.
# In practice this converges in one or two steps; the bound only guards against pathological input.
_MAX_STEPS = 16


def _name_step(s: str) -> str:
    text = unicodedata.normalize("NFKC", s).upper()
    text = unicodedata.normalize("NFKC", text)
    return " ".join(text.split())


def _fixed_point(step, s: str) -> str:  # type: ignore[no-untyped-def]
    current = s
    for _ in range(_MAX_STEPS):
        nxt = step(current)
        if nxt == current:
            return current
        current = nxt
    return current


def normalise_name(s: str) -> str:
    """NFKC, upper-case, collapse whitespace, strip. Idempotent.

    "  ikan   bawal\u00a0hitam " → "IKAN BAWAL HITAM".
    """
    return _fixed_point(_name_step, str(s))


def _term_step(s: str) -> str:
    return normalise_name(s).lower()


def normalise_term(s: str) -> str:
    """normalise_name(s).lower(); used for alias/allergy matching. Idempotent.

    "IKAN Bawal  HITAM" → "ikan bawal hitam".
    """
    return _fixed_point(_term_step, str(s))
