"""Live publishing against the Supabase dev project (task 4.16; Requirements 7.1, 7.2, 7.5).

Skipped unless SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are available via
hargaikan.config.get_secret (a project-root .env is loaded first if present), and always skipped
when APP_ENV is production.

What it does, in a development/test project only:
  1. Publishes a small SYNTHETIC batch twice → the second publish is already live and the live
     rows are identical.
  2. Publishes a second batch whose staged validation is forced to fail → live rows unchanged and
     no staged rows of that batch remain.
  3. Cleans up: deletes every fact row of both test batches, restores the fact rows that were
     live before the test (promote_batch had moved them to 'retired'), and deletes the test
     species and premis location. Note: promote_batch deletes the previous 'retired' rollback
     copy of the touched tables, so a rollback copy that existed before the test is not restored.
"""

from __future__ import annotations

import uuid
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from hargaikan.config import APP_ENV, SUPABASE_SERVICE_ROLE_KEY, SUPABASE_URL, Settings, get_secret
from hargaikan.domain.ids import species_id
from hargaikan.domain.models import Species, SpeciesAlias
from hargaikan.pipeline.bands import build_bands
from hargaikan.pipeline.cleaning import normalise_premise
from hargaikan.pipeline.locations import build_hierarchy, by_id
from hargaikan.pipeline.publish import Batch, make_batch, publish
from hargaikan.pipeline.synthetic import generate
from hargaikan.repositories.publish_repo import FACT_TABLES, SupabasePublishRepository

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def client() -> Any:
    env_file = ROOT / ".env"
    if env_file.is_file():
        from dotenv import load_dotenv

        load_dotenv(env_file, override=False)
    missing = [n for n in (SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY) if not get_secret(n)]
    if missing:
        pytest.skip(f"Supabase credentials not configured: {', '.join(missing)}")
    if (get_secret(APP_ENV) or "development") == "production":
        pytest.skip("live publish test never runs against production")
    from supabase import create_client

    return create_client(get_secret(SUPABASE_URL), get_secret(SUPABASE_SERVICE_ROLE_KEY))


def _live_ids(client: Any, table: str) -> set[str]:
    ids: set[str] = set()
    start = 0
    while True:
        rows = client.table(table).select("id").eq("publish_status", "live").range(start, start + 999).execute().data
        ids.update(r["id"] for r in rows)
        if len(rows) < 1000:
            return ids
        start += 1000


def _rows_of(client: Any, table: str, batch_id: str, status: str) -> list[dict]:
    return client.table(table).select("*").eq("batch_id", batch_id).eq("publish_status", status).execute().data


def _test_batch(tag: str, seed: int) -> Batch:
    code = f"ZZ_TEST_PUBLISH_{tag}"
    sp = Species(species_id(code), code, f"UJIAN PUBLISH {tag}", None, (), True)
    premise = normalise_premise(f"ZZ TEST PUBLISH ({tag})")
    hierarchy = [loc for loc in build_hierarchy([premise], {}) if loc.level != "pasar"]
    premis = [loc for loc in hierarchy if loc.level == "premis"]
    records = generate([sp], premis, weeks=2, seed=seed, start=date(2026, 1, 5))
    bands = build_bands(records, by_id(hierarchy), Settings())
    return make_batch("prices", species=[sp], aliases=[SpeciesAlias(sp.id, sp.canonical_name_ms, "raw")],
                      locations=hierarchy, records=records, bands=bands)


class _ForcedStagedFailure(SupabasePublishRepository):
    def validate_staged(self, batch: Batch) -> list[str]:
        return [*super().validate_staged(batch), "forced_staged_failure"]


def test_publish_twice_and_forced_staged_failure(client: Any) -> None:
    tag = uuid.uuid4().hex[:8].upper()
    batch, other = _test_batch(tag, seed=1), _test_batch(tag, seed=2)
    assert batch.batch_id != other.batch_id
    previously_live = {t: _live_ids(client, t) for t in FACT_TABLES}
    repo = SupabasePublishRepository(client)
    try:
        first = publish(batch, repo, "development")
        assert first.published and not first.already_live, first.errors
        live_once = {t: _rows_of(client, t, batch.batch_id, "live") for t in FACT_TABLES}
        assert len(live_once["price_records"]) == len(batch.records)
        assert len(live_once["price_bands"]) == len(batch.bands)

        second = publish(batch, repo, "development")
        assert second.published and second.already_live
        assert {t: _rows_of(client, t, batch.batch_id, "live") for t in FACT_TABLES} == live_once

        failed = publish(other, _ForcedStagedFailure(client), "development")
        assert not failed.published and "forced_staged_failure" in failed.errors
        assert {t: _rows_of(client, t, batch.batch_id, "live") for t in FACT_TABLES} == live_once
        assert all(not _rows_of(client, t, other.batch_id, "staged") for t in FACT_TABLES)
    finally:
        for t in FACT_TABLES:
            for bid in (batch.batch_id, other.batch_id):
                client.table(t).delete().eq("batch_id", bid).execute()
            ids = sorted(previously_live[t])
            for i in range(0, len(ids), 200):
                client.table(t).update({"publish_status": "live"}).in_("id", ids[i : i + 200]).execute()
        sp_id = batch.species[0].id
        client.table("species").delete().eq("id", sp_id).execute()  # cascades to species_aliases
        premis = next(loc for loc in batch.locations if loc.level == "premis")
        client.table("locations").delete().eq("id", premis.id).execute()
