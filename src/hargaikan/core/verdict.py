"""Verdict engine: where a user's quote sits in the historical P25–P75 band (layer 1, pure).

The verdict is a deterministic three-way classification computed only from the entered
price, P25 and P75. It takes no AI, model or seller-related input. Inclusive bounds belong
to "Dalam julat sejarah".

Pure logic only: no supabase, pinecone, requests or streamlit imports and no I/O.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum

from hargaikan.domain import messages as m
from hargaikan.domain.models import PriceBand

ICON_BELOW = "⬇️"
ICON_WITHIN = "✅"
ICON_ABOVE = "⬆️"
ICON_NO_DATA = "ℹ️"


class VerdictKind(str, Enum):
    BELOW = "below"
    WITHIN = "within"
    ABOVE = "above"
    NO_DATA = "no_data"


@dataclass(frozen=True)
class Verdict:
    kind: VerdictKind
    text: str
    icon: str


class InvalidPriceError(ValueError):
    """Raised when a price is non-numeric, boolean, not finite, or ≤ 0."""


def parse_price(raw: object) -> float:
    """Parse a user-entered price such as ``18``, ``"18"``, ``"RM18.50"`` or ``"1,200.00"``.

    Raises InvalidPriceError for None, booleans, non-numeric text, NaN, infinity and values ≤ 0.
    """
    if raw is None or isinstance(raw, bool):
        raise InvalidPriceError(m.INVALID_PRICE)
    try:
        x = float(str(raw).upper().replace("RM", "").replace(",", "").strip())
    except (ValueError, TypeError, OverflowError):
        raise InvalidPriceError(m.INVALID_PRICE) from None
    if not math.isfinite(x) or x <= 0:
        raise InvalidPriceError(m.INVALID_PRICE)
    return x


def compute_verdict(price: float, band: PriceBand | None) -> Verdict:
    """Classify ``price`` (from ``parse_price``) against ``band``'s P25 and P75.

    price < P25 → below; P25 ≤ price ≤ P75 → within; price > P75 → above; no band → no data.
    """
    if band is None:
        return Verdict(VerdictKind.NO_DATA, m.NO_DATA, ICON_NO_DATA)
    if price < band.p25:
        return Verdict(VerdictKind.BELOW, m.VERDICT_BELOW, ICON_BELOW)
    if price <= band.p75:
        return Verdict(VerdictKind.WITHIN, m.VERDICT_WITHIN, ICON_WITHIN)
    return Verdict(VerdictKind.ABOVE, m.VERDICT_ABOVE, ICON_ABOVE)
