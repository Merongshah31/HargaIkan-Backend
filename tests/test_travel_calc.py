"""Unit tests for hargaikan.core.travel_calc, including the Requirement 21.13 example."""

from __future__ import annotations

import ast
from decimal import Decimal
from pathlib import Path

import pytest

from hargaikan.core import travel_calc
from hargaikan.core.travel_calc import (
    NOT_ESTIMATED,
    InvalidTravelInput,
    TravelInput,
    compute_travel,
    compute_trip,
    round_money,
)
from hargaikan.domain import messages as m

D = Decimal


def test_spec_example_one_way() -> None:
    """RM18/kg × 2 kg, toll RM6, fuel RM5 + parking RM3, one-way → fish RM36, total RM50, RM25/kg.

    Fuel RM5 is modelled as 20 km ÷ 10 km/L = 2 L × RM2.50/L (all user-entered values).
    """
    t = TravelInput(
        mode="kereta",
        distance_km=D("20"),
        fuel_efficiency_km_per_l=D("10"),
        fuel_type="RON95",
        fuel_price_rm_per_l=D("2.50"),
        toll_rm=D("6"),
        parking_rm=D("3"),
        return_trip=False,
    )
    r = compute_trip(t, D("18"), D("2"))
    assert r.fuel_litres == D("2") and r.fuel_cost == D("5") and r.toll == D("6") and r.parking == D("3")
    assert r.fuel_cost + r.parking == D("8")
    assert r.travel_total == D("14")
    assert r.fish_cost == D("36")
    assert r.grand_total == D("50")
    assert r.effective_rm_per_kg == D("25")
    assert r.message is None
    assert round_money(r.effective_rm_per_kg) == D("25.00")


def test_return_trip_doubles_fuel_toll_not_parking() -> None:
    t = TravelInput("kereta", D("20"), D("10"), "RON97", D("2.50"), toll_rm=D("6"), parking_rm=D("3"), return_trip=True)
    r = compute_travel(t)
    assert (r.fuel_litres, r.fuel_cost, r.toll, r.parking, r.travel_total) == (D(4), D(10), D(12), D(3), D(25))


def test_public_transport_fare_only() -> None:
    t = TravelInput("pengangkutan_awam", transit_fare_rm=D("2.40"), parking_rm=D("5"), return_trip=True)
    r = compute_travel(t)
    assert r.travel_total == r.transit_fare == D("4.80")
    assert r.fuel_cost is None and r.parking is None


@pytest.mark.parametrize(
    "t",
    [
        TravelInput("kereta", distance_km=D("10"), fuel_efficiency_km_per_l=D("12")),
        TravelInput("kereta", fuel_efficiency_km_per_l=D("12"), fuel_price_rm_per_l=D("2.05")),
        TravelInput("pengangkutan_awam", distance_km=D("10")),
    ],
)
def test_missing_mode_inputs_not_estimated(t: TravelInput) -> None:
    assert compute_travel(t) == NOT_ESTIMATED
    r = compute_trip(t, D("18"), D("2"))
    assert r.travel_total is None and r.message == m.TRAVEL_NOT_ESTIMATED
    assert r.fish_cost == D("36") and r.grand_total is None and r.effective_rm_per_kg is None


def test_missing_fish_inputs_not_computed() -> None:
    t = TravelInput("pengangkutan_awam", transit_fare_rm=D("3"))
    for price, qty in [(None, D("2")), (D("18"), None), (None, None)]:
        r = compute_trip(t, price, qty)
        assert r.travel_total == D("3")
        assert r.fish_cost is None and r.grand_total is None and r.effective_rm_per_kg is None


def test_no_travel_input() -> None:
    r = compute_trip(None, D("18"), D("2"))
    assert r.fish_cost == D("36") and r.travel_total is None and r.message == m.TRAVEL_NOT_ESTIMATED


@pytest.mark.parametrize(
    ("t", "price", "qty"),
    [
        (TravelInput("kereta", distance_km=D("-1")), None, None),
        (TravelInput("kereta", fuel_efficiency_km_per_l=D("0")), None, None),
        (TravelInput("kereta", toll_rm=D("-0.01")), None, None),
        (TravelInput("pengangkutan_awam", transit_fare_rm=D("-2")), None, None),
        (None, D("0"), D("2")),
        (None, D("18"), D("-1")),
        (TravelInput("kereta", distance_km=D("NaN")), None, None),
    ],
)
def test_invalid_inputs(t: TravelInput | None, price: Decimal | None, qty: Decimal | None) -> None:
    with pytest.raises(InvalidTravelInput):
        compute_trip(t, price, qty)


def test_zero_distance_allowed() -> None:
    r = compute_travel(TravelInput("kereta", D("0"), D("10"), fuel_price_rm_per_l=D("2.05")))
    assert r.travel_total == D("0")


def test_round_money_half_up() -> None:
    assert round_money(D("2.345")) == D("2.35")
    assert round_money(D("2.344")) == D("2.34")
    assert round_money(None) is None


def test_travel_calc_is_pure() -> None:
    """No outside services, Streamlit or forecast imports (Requirements 21.12, 18.10)."""
    tree = ast.parse(Path(travel_calc.__file__).read_text(encoding="utf-8"))
    names = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    names |= {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    banned = ("supabase", "pinecone", "requests", "streamlit", "httpx", "urllib", "hargaikan.forecast")
    assert not [n for n in names if n.startswith(banned)]
