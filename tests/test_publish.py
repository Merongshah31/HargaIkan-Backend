"""Example tests for pipeline.publish and repositories.publish_repo (offline, recording client)."""

from __future__ import annotations

import dataclasses
import uuid
from datetime import date
from typing import Any

from hargaikan.domain.models import Location, PriceRecord, Species, SpeciesAlias
from hargaikan.pipeline.bands import build_bands
from hargaikan.pipeline.cleaning import default_location_id, normalise_premise
from hargaikan.pipeline.locations import NEGERI_ID, build_hierarchy, by_id
from hargaikan.pipeline.publish import batch_id_for, make_batch, publish, validate_batch
from hargaikan.repositories.protocols import PublishRepository
from hargaikan.repositories.publish_repo import SupabasePublishRepository, location_depths
from tests.fakes import FailingPublishRepository, FakePublishRepository, batch_facts
from tests.publish_strategies import SETTINGS

PREMISE = normalise_premise("AEON (AU2)")
SPECIES = Species("11111111-1111-5111-8111-111111111111", "IKAN_KEMBUNG", "IKAN KEMBUNG", None, (), True)


def _batch(price: float = 12.5, source: str = "IKANSIGHT"):
    locations = build_hierarchy([PREMISE], {})
    rec = PriceRecord(SPECIES.id, default_location_id(PREMISE), "IKAN KEMBUNG", None, "kg", price, None, None, 3,
                      date(2026, 10, 12), date(2026, 10, 18), source, 0, "ok")
    bands = build_bands([rec], by_id(locations), SETTINGS)
    return make_batch("prices", species=[SPECIES], aliases=[SpeciesAlias(SPECIES.id, "Ikan  Kembung", "raw")],
                      locations=locations, records=[rec], bands=bands)


def test_batch_id_is_content_derived_and_order_independent() -> None:
    a = _batch()
    assert uuid.UUID(a.batch_id).version == 5
    assert _batch().batch_id == a.batch_id
    assert _batch(price=13.0).batch_id != a.batch_id
    assert batch_id_for([1, 2, 3]) == batch_id_for([3, 1, 2])
    assert batch_id_for((1, 2)) != batch_id_for((2, 1))  # tuples are ordered values
    reordered = make_batch("prices", species=a.species, aliases=a.aliases, locations=list(reversed(a.locations)),
                           records=a.records, bands=list(reversed(a.bands)))
    assert reordered.batch_id == a.batch_id


def test_publish_happy_path_then_already_live() -> None:
    batch, repo = _batch(), FakePublishRepository()
    assert isinstance(repo, PublishRepository)
    report = publish(batch, repo, "development")
    assert report.published and not report.already_live and report.errors == ()
    assert repo.calls == ["known_ids", "is_live", "upsert_reference", "stage", "validate_staged", "promote"]
    assert repo.live_state() == batch_facts(batch)
    again = publish(batch, repo, "development")
    assert again.published and again.already_live
    assert repo.calls[-2:] == ["known_ids", "is_live"]


def test_publish_replaces_previous_live_rows_and_keeps_rollback_copy() -> None:
    repo = FakePublishRepository()
    first, second = _batch(12.5), _batch(14.0)
    publish(first, repo, "development")
    publish(second, repo, "development")
    assert repo.live_state() == batch_facts(second)
    assert {f.batch_id for f in repo.facts if f.status == "retired"} == {first.batch_id}


def test_validation_errors_and_known_ids() -> None:
    batch = _batch()
    records_only = dataclasses.replace(batch, species=[], locations=[])
    errors = validate_batch(records_only, "development", set(), set())
    assert f"unknown_species:{SPECIES.id}" in errors
    assert f"unknown_location:{NEGERI_ID}" in errors
    known_locations = {loc.id for loc in batch.locations}
    assert validate_batch(dataclasses.replace(batch, species=[]), "development", {SPECIES.id}, known_locations) == []
    bad_loc = dataclasses.replace(batch, locations=[Location("n", "Malaysia", "nasional", "x", None, None), *batch.locations])
    assert "bad_location:0" in validate_batch(bad_loc, "development", set(), set())


def test_synthetic_refused_for_production_only() -> None:
    synthetic = _batch(source="SYNTHETIC")
    assert validate_batch(synthetic, "development", set(), set()) == []
    report = publish(synthetic, FakePublishRepository(), "production")
    assert not report.published and "synthetic_to_production" in report.errors


def test_failures_report_type_only_and_discard() -> None:
    report = publish(_batch(), FailingPublishRepository(), "development")
    assert report.errors == ("publish_failed:InjectedFailure",)
    repo = FakePublishRepository(fail_on=["promote"])
    report = publish(_batch(), repo, "development")
    assert report.errors == ("publish_failed:InjectedFailure",) and "discard" in repo.calls
    assert all(not c for c in repo.staged_state().values()) and all(not c for c in repo.live_state().values())


# --- SupabasePublishRepository with a recording client --------------------------------------------


class _Resp:
    def __init__(self, data: Any = None, count: int | None = None) -> None:
        self.data, self.count = data, count


class _Query:
    def __init__(self, client: "RecordingClient", table: str) -> None:
        self.client, self.table, self.op, self.payload, self.kwargs, self.filters = client, table, None, None, {}, []

    def _set(self, op: str, payload: Any = None, **kwargs: Any) -> "_Query":
        self.op, self.payload, self.kwargs = op, payload, kwargs
        return self

    def select(self, *cols: str, **kw: Any) -> "_Query":
        return self._set("select", cols, **kw)

    def upsert(self, rows: Any, **kw: Any) -> "_Query":
        return self._set("upsert", rows, **kw)

    def insert(self, rows: Any, **kw: Any) -> "_Query":
        return self._set("insert", rows, **kw)

    def eq(self, col: str, value: Any) -> "_Query":
        self.filters.append((col, value))
        return self

    def range(self, a: int, b: int) -> "_Query":
        self.filters.append(("range", (a, b)))
        return self

    def limit(self, n: int) -> "_Query":
        return self

    def execute(self) -> _Resp:
        self.client.log.append((self.table, self.op, self.payload, dict(self.kwargs), list(self.filters)))
        if self.op == "insert":
            self.client.staged[self.table] = self.client.staged.get(self.table, 0) + len(self.payload)
        if self.op == "select" and self.kwargs.get("head"):
            return _Resp(count=self.client.staged.get(self.table, 0))
        return _Resp(data=[])


class RecordingClient:
    def __init__(self) -> None:
        self.log: list[tuple] = []
        self.staged: dict[str, int] = {}

    def table(self, name: str) -> _Query:
        return _Query(self, name)

    def rpc(self, name: str, params: dict) -> _Query:
        q = _Query(self, f"rpc:{name}")
        q.op, q.payload = "rpc", params
        return q


def test_supabase_repo_serialises_rows_and_orders_parents_first() -> None:
    client, batch = RecordingClient(), _batch()
    repo = SupabasePublishRepository(client, chunk_size=2)
    report = publish(batch, repo, "development")
    assert report.published, report.errors

    upserts = [(t, rows, kw) for t, op, rows, kw, _ in client.log if op == "upsert"]
    loc_levels = [[r["level"] for r in rows] for t, rows, _ in upserts if t == "locations"]
    flat = [lvl for chunk in loc_levels for lvl in chunk]
    assert flat.index("nasional") < flat.index("negeri") < flat.index("pasar")
    assert all(kw["on_conflict"] == "level,name" for t, _, kw in upserts if t == "locations")
    premis = next(r for t, rows, _ in upserts if t == "locations" for r in rows if r["level"] == "premis")
    assert (premis["chain"], premis["branch"]) == ("AEON", "AU2")
    alias = next(rows[0] for t, rows, _ in upserts if t == "species_aliases")
    assert alias["alias_norm"] == "ikan kembung"
    assert next(kw for t, _, kw in upserts if t == "species_aliases")["on_conflict"] == "species_id,alias_norm"

    inserts = [(t, rows) for t, op, rows, _, _ in client.log if op == "insert"]
    record = next(rows[0] for t, rows in inserts if t == "price_records")
    assert record["date_start"] == "2026-10-12" and record["publish_status"] == "staged"
    assert record["batch_id"] == batch.batch_id and isinstance(record["species_id"], str)
    assert all(len(rows) <= 2 for _, rows in inserts)
    rpcs = [(t, payload) for t, op, payload, _, _ in client.log if op == "rpc"]
    assert rpcs == [("rpc:promote_batch", {"p_batch": batch.batch_id})]


def test_supabase_repo_staged_count_mismatch_discards() -> None:
    class ShortClient(RecordingClient):
        def table(self, name: str) -> _Query:
            q = super().table(name)
            if name == "price_bands":
                q.execute = lambda: (_Resp(count=0) if q.kwargs.get("head") else _Query.execute(q))  # type: ignore[method-assign]
            return q

    client = ShortClient()
    report = publish(_batch(), SupabasePublishRepository(client), "development")
    assert not report.published
    assert any(e.startswith("staged_count_mismatch:price_bands") for e in report.errors)
    assert ("rpc:discard_batch", "rpc") in [(t, op) for t, op, *_ in client.log]


def test_location_depths_handles_external_parents() -> None:
    locs = [Location("c", "C", "premis", "b", None, None), Location("b", "B", "negeri", "a", None, None),
            Location("x", "X", "premis", "outside", None, None)]
    assert location_depths(locs) == {"c": 1, "b": 0, "x": 0}
