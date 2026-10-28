"""Property tests for hargaikan.core.travel_calc (Properties 56–58)."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from hargaikan.core.travel_calc import InvalidTravelInput, TravelInput, compute_travel, compute_trip

TOLERANCE = Decimal("1e-18")


def money(min_value: str = "0", max_value: str = "1000") -> st.SearchStrategy[Decimal]:
    return st.decimals(min_value=Decimal(min_value), max_value=Decimal(max_value), places=2,
                       allow_nan=False, allow_infinity=False)


efficiency = money("0.1", "50")
positive_money = money("0.01", "1000")


@st.composite
def kereta_inputs(draw: st.DrawFn, complete: bool = True) -> TravelInput:
    return TravelInput(
        mode="kereta",
        distance_km=draw(money("0", "500")) if complete else draw(st.none() | money("0", "500")),
        fuel_efficiency_km_per_l=draw(efficiency) if complete else draw(st.none() | efficiency),
        fuel_type=draw(st.sampled_from(["RON95", "RON97"])),
        fuel_price_rm_per_l=draw(money("0", "10")) if complete else draw(st.none() | money("0", "10")),
        toll_rm=draw(money("0", "100")),
        parking_rm=draw(money("0", "100")),
        transit_fare_rm=draw(st.none() | money("0", "50")),
        return_trip=draw(st.booleans()),
    )


@st.composite
def transit_inputs(draw: st.DrawFn, complete: bool = True) -> TravelInput:
    return TravelInput(
        mode="pengangkutan_awam",
        distance_km=draw(st.none() | money("0", "500")),
        fuel_efficiency_km_per_l=draw(st.none() | efficiency),
        fuel_price_rm_per_l=draw(st.none() | money("0", "10")),
        toll_rm=draw(money("0", "100")),
        parking_rm=draw(money("0", "100")),
        transit_fare_rm=draw(money("0", "50")) if complete else draw(st.none() | money("0", "50")),
        return_trip=draw(st.booleans()),
    )


def _close(a: Decimal, b: Decimal) -> bool:
    return abs(a - b) <= TOLERANCE


# Feature: hargaikan-v2, Property 56: Travel formulas
@given(t=kereta_inputs() | transit_inputs())
def test_travel_formulas(t: TravelInput) -> None:
    """*For any* non-negative valid travel input: in kereta mode with distance, efficiency and fuel price
    present, fuel litres = distance ÷ efficiency × m and travel total = litres × price + toll × m + parking,
    where m = 2 for a return trip and 1 otherwise. In pengangkutan_awam mode with a fare, travel total =
    fare × m. The difference between return and one-way totals equals the one-way fuel + toll (or fare).

    **Validates: Requirements 21.1, 21.2, 21.3, 21.4, 21.5**
    """
    mult = Decimal(2) if t.return_trip else Decimal(1)
    tb = compute_travel(t)
    one_way = compute_travel(replace(t, return_trip=False))
    two_way = compute_travel(replace(t, return_trip=True))
    assert tb.message is None
    if t.mode == "kereta":
        litres = t.distance_km / t.fuel_efficiency_km_per_l * mult
        assert tb.fuel_litres == litres
        assert tb.fuel_cost == litres * t.fuel_price_rm_per_l
        assert tb.toll == t.toll_rm * mult and tb.parking == t.parking_rm and tb.transit_fare is None
        assert tb.travel_total == litres * t.fuel_price_rm_per_l + t.toll_rm * mult + t.parking_rm
        assert _close(two_way.travel_total - one_way.travel_total, one_way.fuel_cost + one_way.toll)
        assert two_way.parking == one_way.parking  # parking counted once per visit
    else:
        assert tb.travel_total == tb.transit_fare == t.transit_fare_rm * mult
        assert tb.fuel_litres is tb.fuel_cost is tb.toll is tb.parking is None
        assert two_way.travel_total - one_way.travel_total == one_way.transit_fare
    assert all(isinstance(x, Decimal) for x in (tb.travel_total, one_way.travel_total, two_way.travel_total))


# Feature: hargaikan-v2, Property 57: Trip totals and missing values
@given(
    t=st.none() | kereta_inputs(complete=False) | transit_inputs(complete=False),
    price=st.none() | positive_money,
    qty=st.none() | money("0.01", "100"),
)
def test_trip_totals_and_missing_values(t: TravelInput | None, price: Decimal | None, qty: Decimal | None) -> None:
    """*For any* travel input (or None), positive fish price and positive quantity (each possibly missing):
    fish cost = price × qty when both are present, total = fish cost + travel total when both are computed,
    effective = total ÷ qty, and each is None otherwise. When travel inputs for the mode are missing, travel
    total is None and the message is "Kos perjalanan belum dianggarkan".

    **Validates: Requirements 21.6, 21.7, 21.8, 21.9**
    """
    r = compute_trip(t, price, qty)
    if price is not None and qty is not None:
        assert r.fish_cost == price * qty
    else:
        assert r.fish_cost is None
    if t is None:
        missing = True
    elif t.mode == "kereta":
        missing = None in (t.distance_km, t.fuel_efficiency_km_per_l, t.fuel_price_rm_per_l)
    else:
        missing = t.transit_fare_rm is None
    if missing:
        assert r.travel_total is None and r.message == "Kos perjalanan belum dianggarkan"
    else:
        assert r.travel_total == compute_travel(t).travel_total and r.message is None
    if r.fish_cost is not None and r.travel_total is not None:
        assert r.grand_total == r.fish_cost + r.travel_total
        assert r.effective_rm_per_kg == r.grand_total / qty
    else:
        assert r.grand_total is None and r.effective_rm_per_kg is None


negative = money("-1000", "-0.01")
non_positive = money("-1000", "0")
_AMOUNT_FIELDS = ("distance_km", "fuel_price_rm_per_l", "toll_rm", "parking_rm", "transit_fare_rm")


@st.composite
def invalid_travel_cases(draw: st.DrawFn) -> tuple[TravelInput, Decimal | None, Decimal | None]:
    base = draw(kereta_inputs(complete=False) | transit_inputs(complete=False))
    price = draw(st.none() | positive_money)
    qty = draw(st.none() | money("0.01", "100"))
    kind = draw(st.sampled_from(["amount", "efficiency", "price", "qty"]))
    if kind == "amount":
        base = replace(base, **{draw(st.sampled_from(_AMOUNT_FIELDS)): draw(negative)})
    elif kind == "efficiency":
        base = replace(base, fuel_efficiency_km_per_l=draw(non_positive))
    elif kind == "price":
        price = draw(non_positive)
    else:
        qty = draw(non_positive)
    return base, price, qty


# Feature: hargaikan-v2, Property 58: Invalid travel inputs are rejected
@given(case=invalid_travel_cases())
def test_invalid_travel_inputs_are_rejected(case: tuple[TravelInput, Decimal | None, Decimal | None]) -> None:
    """*For any* travel input with a negative amount or fuel efficiency ≤ 0, and *for any* fish price or
    quantity ≤ 0, the Travel_Calculator raises InvalidTravelInput.

    **Validates: Requirements 21.10**
    """
    t, price, qty = case
    with pytest.raises(InvalidTravelInput):
        compute_trip(t, price, qty)
    travel_invalid = any(getattr(t, f) is not None and getattr(t, f) < 0 for f in _AMOUNT_FIELDS) or (
        t.fuel_efficiency_km_per_l is not None and t.fuel_efficiency_km_per_l <= 0
    )
    if travel_invalid:
        with pytest.raises(InvalidTravelInput):
            compute_travel(t)
