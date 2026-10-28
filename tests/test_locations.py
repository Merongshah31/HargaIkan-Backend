"""Unit tests for pipeline.locations.build_hierarchy and core.location_fallback.ancestors (Req 6.1, 6.2)."""

from __future__ import annotations

from types import SimpleNamespace

from hargaikan.core.location_fallback import ancestors
from hargaikan.domain.ids import location_id
from hargaikan.domain.models import Location
from hargaikan.pipeline.cleaning import default_location_id, normalise_premise
from hargaikan.pipeline.locations import (
    NASIONAL_ID,
    NEGERI_ID,
    PASAR_NAMES,
    PremiseLocation,
    build_hierarchy,
    by_id,
)


def _premises():
    return [normalise_premise(p) for p in ("AEON ( AU2 )", "NSK GROCER ( TIMES SQUARE )", "aeon (au2)", "MYDIN")]


def test_hierarchy_levels_and_parents() -> None:
    locs = build_hierarchy(_premises(), {})
    assert [loc.level for loc in locs[:2]] == ["nasional", "negeri"]
    assert locs[0].name == "Malaysia" and locs[0].parent_id is None
    assert locs[1].name == "Kuala Lumpur" and locs[1].parent_id == NASIONAL_ID
    pasar = [loc for loc in locs if loc.level == "pasar"]
    assert tuple(loc.name for loc in pasar) == PASAR_NAMES
    assert all(loc.parent_id == NEGERI_ID for loc in pasar)
    premis = [loc for loc in locs if loc.level == "premis"]
    assert [loc.name for loc in premis] == ["AEON (AU2)", "MYDIN", "NSK GROCER (TIMES SQUARE)"]  # one per branch
    assert all(loc.parent_id == NEGERI_ID for loc in premis)
    ids = [loc.id for loc in locs]
    assert len(ids) == len(set(ids))


def test_ids_are_deterministic_and_match_cleaning() -> None:
    a, b = build_hierarchy(_premises(), {}), build_hierarchy(list(reversed(_premises())), {})
    assert a == b
    for p in _premises():
        assert default_location_id(p) in by_id(a)
        assert default_location_id(p) == location_id("premis", p.key)
    assert by_id(a)[location_id("pasar", "Pasar Chow Kit")].name == "Pasar Chow Kit"


def test_premis_carries_chain_and_branch() -> None:
    locs = by_id(build_hierarchy(_premises(), {}))
    aeon = locs[default_location_id(normalise_premise("AEON (AU2)"))]
    assert isinstance(aeon, PremiseLocation)
    assert (aeon.chain, aeon.branch) == ("AEON", "AU2")
    mydin = locs[default_location_id(normalise_premise("MYDIN"))]
    assert (mydin.chain, mydin.branch) == ("MYDIN", None)


def test_coordinates_only_from_ok_or_manual_entries() -> None:
    geocodes = {
        "AEON (AU2)": SimpleNamespace(lat=3.21, lng=101.75, status="ok"),
        "MYDIN": {"lat": "3.10", "lng": "101.70", "status": "manual"},
        "NSK GROCER (TIMES SQUARE)": SimpleNamespace(lat=1.0, lng=103.0, status="out_of_area"),
        "Pasar Pudu": SimpleNamespace(lat=None, lng=None, status="not_found"),
    }
    locs = {loc.name: loc for loc in build_hierarchy(_premises(), geocodes)}
    assert (locs["AEON (AU2)"].lat, locs["AEON (AU2)"].lng) == (3.21, 101.75)
    assert (locs["MYDIN"].lat, locs["MYDIN"].lng) == (3.10, 101.70)
    assert (locs["NSK GROCER (TIMES SQUARE)"].lat, locs["NSK GROCER (TIMES SQUARE)"].lng) == (None, None)
    assert locs["Pasar Pudu"].lat is None and locs["Pasar Chow Kit"].lat is None
    assert locs["Malaysia"].lat is None


def test_ancestors_nearest_first() -> None:
    locs = by_id(build_hierarchy(_premises(), {}))
    pid = default_location_id(normalise_premise("MYDIN"))
    assert [loc.name for loc in ancestors(pid, locs)] == ["Kuala Lumpur", "Malaysia"]
    assert ancestors(NASIONAL_ID, locs) == []
    assert ancestors("unknown", locs) == []


def test_ancestors_terminates_on_cycles_and_missing_parents() -> None:
    cyc = {
        "a": Location("a", "A", "premis", "b", None, None),
        "b": Location("b", "B", "negeri", "c", None, None),
        "c": Location("c", "C", "nasional", "a", None, None),
    }
    assert [loc.id for loc in ancestors("a", cyc)] == ["b", "c"]
    self_loop = {"x": Location("x", "X", "premis", "x", None, None)}
    assert ancestors("x", self_loop) == []
    dangling = {"y": Location("y", "Y", "premis", "missing", None, None)}
    assert ancestors("y", dangling) == []
