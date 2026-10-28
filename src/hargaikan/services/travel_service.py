"""Travel service: trip cost from user input and the maintained fare table only (Requirement 21).

Layer 3 (no FastAPI imports). Uses :func:`hargaikan.core.travel_calc.compute_trip` (Decimal
arithmetic; rounding happens only for display in the API layer).

* Distance, fuel efficiency, fuel price (RON95/RON97), toll and parking are always user input
  ("Input pengguna"). Automatic routing (OpenStreetMap) is a deferred ``RoutingService``
  (see :mod:`hargaikan.services.deferred`) and is not used in the MVP.
* Public transport fare: the user's fare if given ("Input pengguna"); else the maintained fare
  table entry for (transit_mode, origin, destination) ("Jadual tambang", with its source and
  check date); else TRAVEL_NOT_ESTIMATED. No fare is ever invented.
* Travel totals stay separate from the fish cost; the effective cost per kg exists only when
  price and quantity are given. Travel values never feed the forecast.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from hargaikan.core.travel_calc import InvalidTravelInput, TravelBreakdown, TravelInput, compute_trip
from hargaikan.data.fares import FareEntry, FareTableError, lookup_fare
from hargaikan.domain import messages as m
from hargaikan.domain.models import SourceLabel
from hargaikan.services.result import ValidationFailed

logger = logging.getLogger(__name__)

INPUT = SourceLabel.INPUT_PENGGUNA.value
TRAVEL = SourceLabel.ANGGARAN_PERJALANAN.value
FARE_TABLE = m.FARE_TABLE_LABEL

_AMOUNT_FIELDS = ("distance_km", "fuel_price_rm_per_l", "toll_rm", "parking_rm", "transit_fare_rm")
_POSITIVE_FIELDS = ("fuel_efficiency_km_per_l", "fish_price_rm_per_kg", "qty_kg")
_UNITS = {
    "distance_km": "km",
    "fuel_efficiency_km_per_l": "km/l",
    "fuel_price_rm_per_l": "RM/l",
    "fuel_litres": "l",
    "qty_kg": "kg",
    "fish_price_rm_per_kg": "RM/kg",
    "effective_rm_per_kg": "RM/kg",
}


@dataclass(frozen=True)
class TravelRequest:
    """Raw user input; amounts may be numbers or strings such as "RM6.00"."""

    mode: str
    distance_km: object = None
    fuel_efficiency_km_per_l: object = None
    fuel_type: str = "RON95"
    fuel_price_rm_per_l: object = None
    toll_rm: object = None
    parking_rm: object = None
    transit_fare_rm: object = None
    transit_mode: str | None = None  # mrt | lrt | monorel | bas (fare table lookup)
    origin: str | None = None
    destination: str | None = None
    return_trip: bool = False
    fish_price_rm_per_kg: object = None
    qty_kg: object = None


@dataclass(frozen=True)
class TravelValue:
    key: str
    name: str  # BM label
    value: Decimal | None  # None → display NOT_COMPUTED
    unit: str
    source_label: str  # "Input pengguna", "Jadual tambang" or "Anggaran perjalanan"
    display_label: str  # e.g. "Anggaran perjalanan · Input pengguna"
    note: str | None = None


@dataclass(frozen=True)
class TravelView:
    mode: str
    fuel_type: str
    return_trip: bool
    estimated: bool  # travel_total computed
    message: str | None  # TRAVEL_NOT_ESTIMATED
    inputs: tuple[TravelValue, ...]
    components: tuple[TravelValue, ...]
    travel_total: TravelValue
    fish_cost: TravelValue
    grand_total: TravelValue
    effective_rm_per_kg: TravelValue
    fare_source: str | None  # INPUT or FARE_TABLE when a fare was used
    fare_source_detail: str | None  # fare table entry source + updated_on
    notices: tuple[str, ...] = field(default_factory=tuple)


def _label(key: str) -> str:
    return m.TRAVEL_FIELD_LABELS.get(key, key)


def _parse(key: str, raw: object, errors: dict[str, str]) -> Decimal | None:
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    if isinstance(raw, bool):
        errors[key] = m.TRAVEL_FIELD_NOT_NUMBER.format(field=_label(key))
        return None
    try:
        d = Decimal(str(raw).upper().replace("RM", "").replace(",", "").strip())
    except (InvalidOperation, ValueError):
        errors[key] = m.TRAVEL_FIELD_NOT_NUMBER.format(field=_label(key))
        return None
    if not d.is_finite():
        errors[key] = m.TRAVEL_FIELD_NOT_NUMBER.format(field=_label(key))
        return None
    if key in _AMOUNT_FIELDS and d < 0:
        errors[key] = m.TRAVEL_FIELD_NEGATIVE.format(field=_label(key))
        return None
    if key in _POSITIVE_FIELDS and d <= 0:
        errors[key] = m.TRAVEL_FIELD_NOT_POSITIVE.format(field=_label(key))
        return None
    return d


def _computed(key: str, value: Decimal | None, display: str = m.TRAVEL_COMPUTED_LABEL) -> TravelValue:
    return TravelValue(key, _label(key), value, _UNITS.get(key, "RM"), TRAVEL, display)


class TravelService:
    def __init__(self, fares: Callable[[], Sequence[FareEntry]]) -> None:
        self._fares = fares

    def _fare_entries(self) -> tuple[Sequence[FareEntry], bool]:
        try:
            return self._fares(), True
        except FareTableError as exc:
            logger.warning("transit fare table rejected (%d problem(s))", len(exc.problems))
            return (), False

    def estimate(self, req: TravelRequest) -> TravelView:
        errors: dict[str, str] = {}
        if req.mode not in ("kereta", "pengangkutan_awam"):
            raise ValidationFailed((m.INVALID_REQUEST,), {"mode": m.INVALID_REQUEST})
        if req.fuel_type not in ("RON95", "RON97"):
            raise ValidationFailed((m.INVALID_REQUEST,), {"fuel_type": m.INVALID_REQUEST})
        keys = (*_AMOUNT_FIELDS, *_POSITIVE_FIELDS)
        v = {k: _parse(k, getattr(req, k), errors) for k in keys}
        if errors:
            raise ValidationFailed(tuple(errors.values()), errors)

        notices: list[str] = []
        fare, fare_source, fare_detail = v["transit_fare_rm"], None, None
        if req.mode == "pengangkutan_awam":
            if fare is not None:
                fare_source = INPUT
            elif req.transit_mode and req.origin and req.destination:
                entries, ok = self._fare_entries()
                if not ok:
                    notices.append(m.FARE_TABLE_INVALID)
                elif (entry := lookup_fare(entries, req.transit_mode, req.origin, req.destination)) is not None:
                    fare = Decimal(str(entry.fare_rm))
                    fare_source = FARE_TABLE
                    fare_detail = f"{entry.source} ({entry.updated_on.isoformat()})"

        zero = Decimal("0")
        t = TravelInput(
            mode=req.mode,  # type: ignore[arg-type]
            distance_km=v["distance_km"],
            fuel_efficiency_km_per_l=v["fuel_efficiency_km_per_l"],
            fuel_type=req.fuel_type,  # type: ignore[arg-type]
            fuel_price_rm_per_l=v["fuel_price_rm_per_l"],
            toll_rm=v["toll_rm"] if v["toll_rm"] is not None else zero,
            parking_rm=v["parking_rm"] if v["parking_rm"] is not None else zero,
            transit_fare_rm=fare if req.mode == "pengangkutan_awam" else None,
            return_trip=bool(req.return_trip),
        )
        try:
            tb: TravelBreakdown = compute_trip(t, v["fish_price_rm_per_kg"], v["qty_kg"])
        except InvalidTravelInput as exc:  # defensive: inputs were validated above
            key = str(exc)
            raise ValidationFailed((m.TRAVEL_FIELD_NOT_NUMBER.format(field=_label(key)),), {key: m.INVALID_REQUEST}) from None

        # Inputs as entered (only those given), each with its own source.
        inputs: list[TravelValue] = []
        input_keys = (
            ("distance_km", "fuel_efficiency_km_per_l", "fuel_price_rm_per_l", "toll_rm", "parking_rm")
            if req.mode == "kereta"
            else ()
        )
        for k in input_keys:
            if v[k] is not None:
                note = req.fuel_type if k == "fuel_price_rm_per_l" else None
                inputs.append(TravelValue(k, _label(k), v[k], _UNITS.get(k, "RM"), INPUT, INPUT, note))
        if req.mode == "pengangkutan_awam" and fare is not None:
            src = fare_source or INPUT
            inputs.append(TravelValue("transit_fare_rm", _label("transit_fare_rm"), fare, "RM", src, src, fare_detail))
        for k in ("fish_price_rm_per_kg", "qty_kg"):
            if v[k] is not None:
                inputs.append(TravelValue(k, _label(k), v[k], _UNITS[k], INPUT, INPUT))

        travel_display = m.TRAVEL_COMPUTED_FARE_TABLE_LABEL if fare_source == FARE_TABLE else m.TRAVEL_COMPUTED_LABEL
        if req.mode == "kereta":
            components = (
                _computed("fuel_litres", tb.fuel_litres),
                _computed("fuel_cost", tb.fuel_cost),
                _computed("toll_rm", tb.toll),
                _computed("parking_rm", tb.parking),
            )
        else:
            components = (_computed("transit_fare_rm", tb.transit_fare, travel_display),)

        return TravelView(
            mode=req.mode,
            fuel_type=req.fuel_type,
            return_trip=bool(req.return_trip),
            estimated=tb.travel_total is not None,
            message=tb.message,
            inputs=tuple(inputs),
            components=components,
            travel_total=_computed("travel_total", tb.travel_total, travel_display),
            fish_cost=_computed("fish_cost", tb.fish_cost),
            grand_total=_computed("grand_total", tb.grand_total, travel_display),
            effective_rm_per_kg=_computed("effective_rm_per_kg", tb.effective_rm_per_kg, travel_display),
            fare_source=fare_source,
            fare_source_detail=fare_detail,
            notices=tuple(notices),
        )
