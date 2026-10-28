"""Travel_Calculator: trip cost from user input only (layer 1, pure).

All arithmetic uses ``Decimal``. Values are rounded half-up to 2 dp only for display
(see ``round_money``). The calculator makes no outside routing, fuel price or toll calls,
and its values are never passed to the forecast pipeline.

Design decision (return trip): fuel, toll and transit fare depend on distance, so they double
for a return trip. Parking is paid once per visit, so it does not double.

Pure logic only: no supabase, pinecone, requests or streamlit imports and no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from hargaikan.domain import messages as m

TravelMode = Literal["kereta", "pengangkutan_awam"]
FuelType = Literal["RON95", "RON97"]

_ZERO = Decimal("0")
_CENT = Decimal("0.01")


@dataclass(frozen=True)
class TravelInput:
    mode: TravelMode
    distance_km: Decimal | None = None  # one-way, user input
    fuel_efficiency_km_per_l: Decimal | None = None  # user input
    fuel_type: FuelType = "RON95"
    fuel_price_rm_per_l: Decimal | None = None  # user input, no API
    toll_rm: Decimal = _ZERO  # one-way, user input
    parking_rm: Decimal = _ZERO  # per visit, user input
    transit_fare_rm: Decimal | None = None  # one-way, user input
    return_trip: bool = False


@dataclass(frozen=True)
class TravelBreakdown:
    fuel_litres: Decimal | None
    fuel_cost: Decimal | None
    toll: Decimal | None
    parking: Decimal | None
    transit_fare: Decimal | None
    travel_total: Decimal | None
    fish_cost: Decimal | None
    grand_total: Decimal | None
    effective_rm_per_kg: Decimal | None
    message: str | None  # TRAVEL_NOT_ESTIMATED when travel_total is None


class InvalidTravelInput(ValueError):
    """Raised for a negative amount, fuel efficiency ≤ 0, or fish price / quantity ≤ 0."""


NOT_ESTIMATED = TravelBreakdown(None, None, None, None, None, None, None, None, None, m.TRAVEL_NOT_ESTIMATED)


def _check_finite(name: str, v: Decimal | None) -> None:
    if v is not None and not Decimal(v).is_finite():
        raise InvalidTravelInput(name)


def compute_travel(t: TravelInput) -> TravelBreakdown:
    """Compute the travel components for one trip, or NOT_ESTIMATED if mode inputs are missing."""
    amounts = {
        "distance_km": t.distance_km,
        "fuel_price_rm_per_l": t.fuel_price_rm_per_l,
        "toll_rm": t.toll_rm,
        "parking_rm": t.parking_rm,
        "transit_fare_rm": t.transit_fare_rm,
    }
    for name, v in {**amounts, "fuel_efficiency_km_per_l": t.fuel_efficiency_km_per_l}.items():
        _check_finite(name, v)
    for name, v in amounts.items():
        if v is not None and v < 0:
            raise InvalidTravelInput(name)
    if t.fuel_efficiency_km_per_l is not None and t.fuel_efficiency_km_per_l <= 0:
        raise InvalidTravelInput("fuel_efficiency_km_per_l")

    mult = Decimal(2) if t.return_trip else Decimal(1)
    if t.mode == "kereta":
        if None in (t.distance_km, t.fuel_efficiency_km_per_l, t.fuel_price_rm_per_l):
            return NOT_ESTIMATED
        litres = t.distance_km / t.fuel_efficiency_km_per_l * mult
        fuel = litres * t.fuel_price_rm_per_l
        toll = (t.toll_rm if t.toll_rm is not None else _ZERO) * mult
        parking = t.parking_rm if t.parking_rm is not None else _ZERO  # once per visit
        return TravelBreakdown(litres, fuel, toll, parking, None, fuel + toll + parking, None, None, None, None)
    if t.mode != "pengangkutan_awam":
        raise InvalidTravelInput("mode")
    if t.transit_fare_rm is None:
        return NOT_ESTIMATED
    fare = t.transit_fare_rm * mult
    return TravelBreakdown(None, None, None, None, fare, fare, None, None, None, None)


def compute_trip(
    t: TravelInput | None,
    fish_price_rm_per_kg: Decimal | None,
    qty_kg: Decimal | None,
) -> TravelBreakdown:
    """Combine travel with fish cost: fish = price × qty, total = fish + travel, effective = total ÷ qty.

    Each value is None when an input it needs is missing. Price or quantity ≤ 0 is rejected.
    """
    price, qty = fish_price_rm_per_kg, qty_kg
    _check_finite("fish_price_rm_per_kg", price)
    _check_finite("qty_kg", qty)
    if price is not None and price <= 0:
        raise InvalidTravelInput("fish_price_rm_per_kg")
    if qty is not None and qty <= 0:
        raise InvalidTravelInput("qty_kg")
    tb = compute_travel(t) if t is not None else NOT_ESTIMATED
    fish = price * qty if price is not None and qty is not None else None
    grand = fish + tb.travel_total if fish is not None and tb.travel_total is not None else None
    effective = grand / qty if grand is not None else None
    return replace(tb, fish_cost=fish, grand_total=grand, effective_rm_per_kg=effective)


def round_money(value: Decimal | None) -> Decimal | None:
    """Round half-up to 2 dp for display only. None stays None."""
    if value is None:
        return None
    return Decimal(value).quantize(_CENT, rounding=ROUND_HALF_UP)
