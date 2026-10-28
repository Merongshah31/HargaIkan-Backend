"""Property tests for hargaikan.pipeline.publish (Properties 17, 18, 19 and 21 of the design)."""

from __future__ import annotations

import dataclasses

from hypothesis import given
from hypothesis import strategies as st

from hargaikan.pipeline.publish import make_batch, publish, validate_batch
from hargaikan.pipeline.synthetic import SYNTHETIC_SOURCE
from tests.fakes import FakePublishRepository, batch_facts
from tests.publish_strategies import DEFECTS, REAL_SOURCE, valid_batches, with_defect

FAILURE_MODES = (None, "known_ids", "is_live", "upsert_reference", "stage", "validate_staged", "promote", "staged_errors")
BATCH_LISTS = ("species", "aliases", "locations", "records", "bands", "predictions", "metrics", "profiles", "similarity")


def expected_after(before: dict, batch) -> dict:
    """Live state after a successful promote: promote_batch swaps only the fact tables in which
    the batch has rows (migration 001), so other tables keep their previous live rows."""
    facts = batch_facts(batch)
    return {t: facts[t] if facts[t] else before[t] for t in before}


def _repo(initial, failure: str | None) -> FakePublishRepository:
    repo = FakePublishRepository(
        fail_on=[failure] if failure not in (None, "staged_errors") else [],
        staged_errors=["staged_check_failed"] if failure == "staged_errors" else [],
    )
    if initial is not None:
        repo.seed_live(initial)
    return repo


# Feature: hargaikan-v2, Property 17: Publishing is atomic
@given(
    data=st.data(),
    initial=st.none() | valid_batches(),
    failure=st.sampled_from(FAILURE_MODES),
    defect=st.none() | st.sampled_from(DEFECTS),
    target=st.sampled_from(["development", "test"]),
)
def test_publishing_is_atomic(data: st.DataObject, initial, failure, defect, target) -> None:
    """**Validates: Requirements 7.1, 7.2, 7.7**

    For any initial live state and any Batch, publishing leaves the live state equal to the
    Batch's fact rows (all validations pass, no step raises) or unchanged (pure validation fails,
    staged validation fails, or staging/promotion raises). Staged rows never remain.
    """
    batch = data.draw(valid_batches())
    if defect is not None:
        batch = data.draw(with_defect(batch, defect))
    repo = _repo(initial, failure)
    before = repo.live_state()
    already_live = initial is not None and initial.batch_id == batch.batch_id and defect is None

    report = publish(batch, repo, target)

    after = repo.live_state()
    assert after in (before, expected_after(before, batch))
    if report.published:
        assert after == expected_after(before, batch)
        assert not report.errors
    else:
        assert after == before
        assert report.errors
    if (defect is not None or failure is not None) and not already_live:
        assert not report.published
    if defect is None and failure is None:
        assert report.published
    assert all(not c for c in repo.staged_state().values())


# Feature: hargaikan-v2, Property 18: Batch validation catches every defect class
@given(data=st.data(), batch=valid_batches(), defect=st.sampled_from(DEFECTS),
       target=st.sampled_from(["development", "test", "production"]))
def test_batch_validation_catches_every_defect(data: st.DataObject, batch, defect: str, target: str) -> None:
    """**Validates: Requirements 7.3**

    For any valid Batch validate_batch returns no errors (real data, any target); for any single
    injected defect (null required field, price ≤ 0, band order violated, record_count < 1,
    P10 > P90, unknown reference, no fact rows) it returns at least one error.
    """
    real = dataclasses.replace(
        batch,
        records=[dataclasses.replace(r, source_name=REAL_SOURCE) for r in batch.records],
        bands=[dataclasses.replace(b, band_source=REAL_SOURCE) for b in batch.bands],
    )
    assert validate_batch(real, target, set(), set()) == []
    broken = data.draw(with_defect(real, defect))
    errors = validate_batch(broken, target, set(), set())
    assert errors
    expected_prefix = {
        "null_field": "null_field:", "bad_price": "bad_price:", "bad_band": "bad_band:", "bad_count": "bad_",
        "p10_gt_p90": "bad_pred:", "unknown_ref": "unknown_", "no_fact_rows": "empty_batch",
    }[defect]
    assert any(e.startswith(expected_prefix) for e in errors), errors


def _shuffled(data: st.DataObject, batch):
    return make_batch(batch.kind, **{name: data.draw(st.permutations(getattr(batch, name))) for name in BATCH_LISTS})


# Feature: hargaikan-v2, Property 19: Publishing is idempotent
@given(data=st.data(), batch=valid_batches(), initial=st.none() | valid_batches(),
       target=st.sampled_from(["development", "test"]))
def test_publishing_is_idempotent(data: st.DataObject, batch, initial, target: str) -> None:
    """**Validates: Requirements 7.4, 7.5, 7.6**

    batch_id_for gives the same id for equal content regardless of row order. Publishing the same
    Batch twice yields the same live state and reference rows as publishing it once, and the
    second report has already_live true.
    """
    assert _shuffled(data, batch).batch_id == batch.batch_id
    rebuilt = make_batch(batch.kind, **{name: getattr(batch, name) for name in BATCH_LISTS})
    assert rebuilt == batch

    once, twice = _repo(initial, None), _repo(initial, None)
    before = once.live_state()
    first = publish(batch, once, target)
    publish(batch, twice, target)
    second = publish(_shuffled(data, batch), twice, target)

    assert first.published
    assert second.published and second.already_live and not second.errors
    assert twice.live_state() == once.live_state() == expected_after(before, batch)
    assert twice.reference_state() == once.reference_state()


# Feature: hargaikan-v2, Property 21: Synthetic data never reaches production
@given(data=st.data(), batch=valid_batches(), initial=st.none() | valid_batches(REAL_SOURCE),
       where=st.sampled_from(["records", "bands", "all"]), defect=st.none() | st.sampled_from(DEFECTS))
def test_synthetic_never_reaches_production(data: st.DataObject, batch, initial, where: str, defect) -> None:
    """**Validates: Requirements 8.3**

    For any Batch containing at least one SYNTHETIC record or band, publishing with target_env
    production returns published false with synthetic_to_production and leaves the live state unchanged.
    """
    if where == "all":
        batch = dataclasses.replace(
            batch,
            records=[dataclasses.replace(r, source_name=SYNTHETIC_SOURCE) for r in batch.records],
            bands=[dataclasses.replace(b, band_source=SYNTHETIC_SOURCE) for b in batch.bands],
        )
    elif where == "records":
        i = data.draw(st.integers(min_value=0, max_value=len(batch.records) - 1))
        records = list(batch.records)
        records[i] = dataclasses.replace(records[i], source_name=SYNTHETIC_SOURCE)
        batch = dataclasses.replace(batch, records=records)
    else:
        i = data.draw(st.integers(min_value=0, max_value=len(batch.bands) - 1))
        bands = list(batch.bands)
        bands[i] = dataclasses.replace(bands[i], band_source=f"{REAL_SOURCE}+{SYNTHETIC_SOURCE}")
        batch = dataclasses.replace(batch, bands=bands)
    if defect is not None and defect != "no_fact_rows":  # keep at least one synthetic row
        batch = data.draw(with_defect(batch, defect))
    repo = _repo(initial, None)
    before = repo.live_state()

    report = publish(batch, repo, "production")

    assert not report.published and not report.already_live
    assert "synthetic_to_production" in report.errors
    assert repo.live_state() == before
    assert all(not c for c in repo.staged_state().values())
    assert "stage" not in repo.calls and "promote" not in repo.calls
