"""Property-based tests for hargaikan.pipeline.schema_check (Schema_Checker)."""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from hargaikan.pipeline.schema_check import EXPECTED_SCHEMA, Problem, compare_schema, exit_code

TABLES = sorted(EXPECTED_SCHEMA)
EXTRA_NAMES = st.text(alphabet="abcdefghijklmnopqrstuvwxyz_", min_size=1, max_size=12).map(lambda s: f"x_{s}")


@st.composite
def removals(draw: st.DrawFn) -> tuple[dict[str, set[str]], dict[str, bool], set[Problem]]:
    """Start from the expected schema, remove arbitrary tables, columns and RLS flags, and add noise.

    Returns (actual_columns, rls, expected_problems).
    """
    removed_tables = draw(st.sets(st.sampled_from(TABLES)))
    actual: dict[str, set[str]] = {}
    rls: dict[str, bool] = {}
    expected: set[Problem] = set()

    for table in TABLES:
        if table in removed_tables:
            expected.add(Problem("missing_table", table))
            # A dropped table may still linger in the RLS map; it must not be reported twice.
            if draw(st.booleans()):
                rls[table] = draw(st.booleans())
            continue

        columns = sorted(EXPECTED_SCHEMA[table])
        removed_columns = draw(st.sets(st.sampled_from(columns)))
        extra_columns = draw(st.sets(EXTRA_NAMES, max_size=3))
        actual[table] = (set(columns) - removed_columns) | extra_columns
        expected.update(Problem("missing_column", table, c) for c in removed_columns)

        # RLS flag: kept (True), removed as False, or removed by omitting the table from the map.
        rls_state = draw(st.sampled_from(["on", "off", "absent"]))
        if rls_state == "on":
            rls[table] = True
        else:
            if rls_state == "off":
                rls[table] = False
            expected.add(Problem("rls_disabled", table))

    # Unexpected extra tables are ignored by the checker.
    for extra in draw(st.sets(EXTRA_NAMES, max_size=3)):
        actual[extra] = draw(st.sets(EXTRA_NAMES, max_size=3))
        rls[extra] = draw(st.booleans())

    return actual, rls, expected


# Feature: hargaikan-v2, Property 3: Schema checker reports exactly what is missing
@given(case=removals())
def test_schema_checker_reports_exactly_what_is_missing(
    case: tuple[dict[str, set[str]], dict[str, bool], set[Problem]],
) -> None:
    """**Validates: Requirements 2.10**"""
    actual, rls, expected = case
    problems = compare_schema(EXPECTED_SCHEMA, actual, rls)

    # Exactly the removed elements, each reported once.
    assert set(problems) == expected
    assert len(problems) == len(set(problems))

    # Non-zero exit if and only if something was removed.
    assert (exit_code(problems) != 0) == bool(expected)

    # Deterministic: independent of mapping/set iteration order.
    reordered_actual = {t: set(sorted(cols, reverse=True)) for t, cols in reversed(list(actual.items()))}
    reordered_rls = dict(reversed(list(rls.items())))
    reordered_expected = dict(reversed(list(EXPECTED_SCHEMA.items())))
    assert compare_schema(reordered_expected, reordered_actual, reordered_rls) == problems
