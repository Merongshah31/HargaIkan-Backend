"""Pure schema comparison for ``scripts/check_schema.py`` (Requirement 2.10).

No I/O here: the script fetches the live schema through the ``public.schema_report()`` RPC
(defined in ``sql/migrations/001_catalog_prices.sql``) and passes the parsed result to
:func:`compare_schema`.

``EXPECTED_SCHEMA`` must stay consistent with the migrations; ``tests/test_migrations.py``
checks that every table's columns here equal the columns created in the SQL files.
"""

from __future__ import annotations

from collections.abc import Mapping, Set
from dataclasses import dataclass
from typing import Literal

ProblemKind = Literal["missing_table", "missing_column", "rls_disabled"]

_KIND_ORDER: dict[str, int] = {"missing_table": 0, "missing_column": 1, "rls_disabled": 2}

# Table -> required columns, for all 16 tables in migrations 001, 002 and 003.
EXPECTED_SCHEMA: dict[str, frozenset[str]] = {
    # 001_catalog_prices.sql
    "species": frozenset(
        {"id", "code", "canonical_name_ms", "canonical_name_en", "scientific_name", "allergen_groups", "active"}
    ),
    "species_aliases": frozenset({"id", "species_id", "alias", "alias_norm", "language"}),
    "fish_profiles": frozenset(
        {"species_id", "texture", "taste", "bone_level", "cooking_methods", "allergen_groups", "notes"}
    ),
    "fish_similarity": frozenset(
        {"from_species_id", "to_species_id", "similarity_score", "reason", "basis", "verified"}
    ),
    "locations": frozenset({"id", "name", "level", "parent_id", "lat", "lng", "chain", "branch"}),
    "price_records": frozenset(
        {
            "id", "species_id", "location_id", "raw_item_name", "grade", "unit", "price_rm_per_kg",
            "price_low", "price_high", "price_min", "price_max", "record_count", "date_start", "date_end",
            "source_name", "source_snapshot", "quality_status", "batch_id", "publish_status",
        }
    ),
    "price_bands": frozenset(
        {
            "id", "species_id", "location_id", "grade", "p25", "median", "p75", "record_count",
            "confidence_level", "period_start", "period_end", "band_source", "batch_id", "publish_status",
        }
    ),
    "price_predictions": frozenset(
        {
            "id", "species_id", "location_id", "target_start", "target_end", "predicted_median", "p10", "p90",
            "method", "model_version", "mae", "last_data_date", "valid_until", "batch_id", "publish_status",
        }
    ),
    "model_metrics": frozenset(
        {
            "id", "model_version", "scope", "species_id", "location_id", "mae", "rmse", "baseline_mae",
            "baseline_rmse", "interval_coverage", "n_test", "decision", "batch_id", "publish_status", "created_at",
        }
    ),
    # 002_users_rls.sql
    "user_profiles": frozenset(
        {
            "user_id", "consent_allergies", "consent_allergies_at", "consent_location", "consent_location_at",
            "created_at", "updated_at",
        }
    ),
    "user_preferences": frozenset(
        {
            "user_id", "preferred_species_ids", "allergy_species_ids", "allergy_terms", "allergy_groups",
            "budget_rm_per_kg", "preferred_location_id", "updated_at",
        }
    ),
    "purchase_history": frozenset(
        {"id", "user_id", "species_id", "location_id", "price_rm_per_kg", "qty_kg", "purchased_on", "created_at"}
    ),
    # 003_future_tables.sql
    "weather_observations": frozenset({"id", "location_id", "observed_at", "metrics", "source"}),
    "weather_forecasts": frozenset(
        {"id", "location_id", "forecast_for", "metrics", "source", "fetched_at", "expires_at"}
    ),
    "seasonal_events": frozenset({"id", "name", "kind", "start_date", "end_date", "source"}),
    "travel_estimates": frozenset(
        {
            "id", "user_id", "origin_location_id", "destination_location_id", "mode", "distance_km",
            "components", "source", "created_at",
        }
    ),
}


@dataclass(frozen=True)
class Problem:
    """One schema problem. ``column`` is set only for ``missing_column``."""

    kind: ProblemKind
    table: str
    column: str | None = None

    def describe(self) -> str:
        if self.kind == "missing_table":
            return f"missing table: {self.table}"
        if self.kind == "missing_column":
            return f"missing column: {self.table}.{self.column}"
        return f"row level security disabled: {self.table}"


def _sort_key(p: Problem) -> tuple[int, str, str]:
    return (_KIND_ORDER[p.kind], p.table, p.column or "")


def compare_schema(
    expected: Mapping[str, Set[str]],
    actual_columns: Mapping[str, Set[str]],
    rls: Mapping[str, bool],
) -> list[Problem]:
    """Report every expected table that is missing, every missing column of a present table and
    every present table without row level security.

    A missing table is reported once as ``missing_table`` (its columns and RLS flag are implied).
    A present table absent from ``rls`` counts as RLS disabled. Extra tables and columns in the
    actual schema are ignored. The result is sorted by (kind, table, column), so it does not
    depend on mapping or set iteration order.
    """
    problems: list[Problem] = []
    for table, columns in expected.items():
        if table not in actual_columns:
            problems.append(Problem("missing_table", table))
            continue
        present = actual_columns[table]
        problems.extend(Problem("missing_column", table, col) for col in columns if col not in present)
        if not rls.get(table, False):
            problems.append(Problem("rls_disabled", table))
    return sorted(problems, key=_sort_key)


def exit_code(problems: list[Problem]) -> int:
    """Process exit status for the Schema_Checker: non-zero if and only if any problem was found."""
    return 1 if problems else 0


def parse_schema_report(payload: object) -> tuple[dict[str, set[str]], dict[str, bool]]:
    """Parse the jsonb returned by ``public.schema_report()``.

    Expected shape: ``{"tables": {"<table>": {"columns": [...], "rls": bool}}}``.
    Raises ValueError on any other shape.
    """
    if not isinstance(payload, Mapping) or not isinstance(payload.get("tables"), Mapping):
        raise ValueError("schema_report: expected an object with a 'tables' object")
    columns: dict[str, set[str]] = {}
    rls: dict[str, bool] = {}
    for table, info in payload["tables"].items():
        if not isinstance(table, str) or not isinstance(info, Mapping):
            raise ValueError("schema_report: each table entry must be an object")
        cols = info.get("columns")
        flag = info.get("rls")
        if not isinstance(cols, list) or not all(isinstance(c, str) for c in cols) or not isinstance(flag, bool):
            raise ValueError(f"schema_report: malformed entry for table {table}")
        columns[table] = set(cols)
        rls[table] = flag
    return columns, rls
