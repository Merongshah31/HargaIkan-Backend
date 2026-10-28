"""Static checks of sql/migrations/*.sql (Requirements 2.1–2.9, 7.1, 14.9, 25.1).

These read the SQL text only. Behaviour on a real database is covered by
tests/integration/test_schema_live.py.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from hargaikan.pipeline.schema_check import EXPECTED_SCHEMA

MIGRATIONS = Path(__file__).resolve().parents[1] / "sql" / "migrations"
FILES = ["001_catalog_prices.sql", "002_users_rls.sql", "003_future_tables.sql"]

ALL_TABLES = {
    "species", "species_aliases", "fish_profiles", "fish_similarity", "locations",
    "price_records", "price_bands", "price_predictions", "model_metrics",
    "user_profiles", "user_preferences", "purchase_history",
    "weather_observations", "weather_forecasts", "seasonal_events", "travel_estimates",
}
CATALOG_TABLES = {
    "species", "species_aliases", "fish_profiles", "fish_similarity", "locations",
    "price_records", "price_bands", "price_predictions", "model_metrics",
    "weather_observations", "weather_forecasts", "seasonal_events",
}
FACT_TABLES = {"price_records", "price_bands", "price_predictions", "model_metrics"}
USER_TABLES = {"user_profiles", "user_preferences", "purchase_history", "travel_estimates"}
SERVICE_FUNCTIONS = ["promote_batch(uuid)", "discard_batch(uuid)", "schema_report()"]

CONSTRAINT_KEYWORDS = {"primary", "unique", "check", "constraint", "foreign", "exclude"}


def _strip_comments(sql: str) -> str:
    return re.sub(r"--[^\n]*", "", sql)


def _normalise(sql: str) -> str:
    """Lower-case, comments removed, whitespace collapsed."""
    return re.sub(r"\s+", " ", _strip_comments(sql)).lower()


SQL = {name: _normalise((MIGRATIONS / name).read_text(encoding="utf-8")) for name in FILES}
ALL_SQL = " ".join(SQL[name] for name in FILES)


def _split_top_level(body: str) -> list[str]:
    parts, depth, current = [], 0, []
    for ch in body:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
    parts.append("".join(current))
    return [p.strip() for p in parts if p.strip()]


def _created_tables(sql: str) -> dict[str, set[str]]:
    """table -> column names, from `create table if not exists` bodies plus `add column if not exists`."""
    tables: dict[str, set[str]] = {}
    for match in re.finditer(r"create table if not exists (?:public\.)?(\w+) \(", sql):
        start = match.end()
        depth, i = 1, start
        while depth:
            depth += {"(": 1, ")": -1}.get(sql[i], 0)
            i += 1
        columns = set()
        for element in _split_top_level(sql[start : i - 1]):
            first = element.split()[0]
            if first not in CONSTRAINT_KEYWORDS:
                columns.add(first)
        tables[match.group(1)] = columns
    for match in re.finditer(r"alter table (?:public\.)?(\w+) add column if not exists (\w+)", sql):
        tables.setdefault(match.group(1), set()).add(match.group(2))
    return tables


CREATED = _created_tables(ALL_SQL)


def test_all_16_tables_are_created_with_if_not_exists() -> None:
    assert set(CREATED) == ALL_TABLES
    assert len(ALL_TABLES) == 16
    # No table is created without `if not exists`.
    assert not re.search(r"create table (?!if not exists)", ALL_SQL)


def test_tables_are_split_across_migrations_as_designed() -> None:
    assert set(_created_tables(SQL["001_catalog_prices.sql"])) == CATALOG_TABLES - {
        "weather_observations", "weather_forecasts", "seasonal_events"
    }
    assert set(_created_tables(SQL["002_users_rls.sql"])) == {"user_profiles", "user_preferences", "purchase_history"}
    assert set(_created_tables(SQL["003_future_tables.sql"])) == {
        "weather_observations", "weather_forecasts", "seasonal_events", "travel_estimates"
    }


@pytest.mark.parametrize("table", sorted(ALL_TABLES))
def test_every_table_enables_row_level_security(table: str) -> None:
    assert f"alter table public.{table} enable row level security;" in ALL_SQL


def test_expected_schema_matches_sql_columns_exactly() -> None:
    assert set(EXPECTED_SCHEMA) == ALL_TABLES
    for table, columns in EXPECTED_SCHEMA.items():
        assert CREATED[table] == set(columns), table


@pytest.mark.parametrize("name", FILES)
def test_every_create_policy_is_preceded_by_drop_policy_if_exists(name: str) -> None:
    sql = SQL[name]
    creates = list(re.finditer(r"create policy (\w+) on (public\.\w+)", sql))
    assert creates, f"{name} has no policies"
    for match in creates:
        policy, table = match.groups()
        drop = f"drop policy if exists {policy} on {table};"
        position = sql.find(drop)
        assert 0 <= position < match.start(), f"{policy} on {table} lacks a preceding drop"
    names = [(m.group(1), m.group(2)) for m in creates]
    assert len(names) == len(set(names)), "duplicate policy names"


@pytest.mark.parametrize("table", sorted(CATALOG_TABLES))
def test_catalog_tables_have_public_select_and_service_only_writes(table: str) -> None:
    using = "publish_status = 'live'" if table in FACT_TABLES else "true"
    assert (
        f"create policy {table}_public_read on public.{table} for select to anon, authenticated using ({using});"
        in ALL_SQL
    )
    assert (
        f"create policy {table}_service_write on public.{table} for all to service_role "
        "using (true) with check (true);" in ALL_SQL
    )
    # No policy on a catalog table grants writes to the public roles.
    for match in re.finditer(rf"create policy \w+ on public\.{table} for (\w+) to ([\w, ]+?) (?:using|with)", ALL_SQL):
        command, roles = match.groups()
        if command != "select":
            assert roles.strip() == "service_role", match.group(0)


@pytest.mark.parametrize("table", sorted(FACT_TABLES))
def test_fact_tables_carry_batch_and_publish_status(table: str) -> None:
    assert {"batch_id", "publish_status"} <= CREATED[table]
    assert f"create unique index if not exists {table}_live_key on public.{table}" in ALL_SQL
    index = re.search(rf"create unique index if not exists {table}_live_key on public\.{table} \((.*?)\) where (.*?);", ALL_SQL)
    assert index and index.group(2) == "publish_status = 'live'"


@pytest.mark.parametrize("table", sorted(USER_TABLES))
def test_user_tables_have_owner_only_policies(table: str) -> None:
    owner = "user_id = auth.uid()"
    for command in ("select", "insert", "update", "delete"):
        match = re.search(rf"create policy {table}_owner_{command} on public\.{table} for {command} to authenticated (.*?);", ALL_SQL)
        assert match, f"{table} lacks owner {command} policy"
        clause = match.group(1)
        if command in ("select", "delete"):
            assert clause == f"using ({owner})"
        elif command == "insert":
            assert clause == f"with check ({owner})"
        else:
            assert clause == f"using ({owner}) with check ({owner})"
    policies = re.findall(rf"create policy \w+ on public\.{table} for \w+ to ([\w, ]+?) ", ALL_SQL)
    assert policies and all(roles == "authenticated" for roles in policies)
    assert re.search(rf"revoke all on table [^;]*public\.{table}[^;]* from anon;", ALL_SQL)


@pytest.mark.parametrize("function", SERVICE_FUNCTIONS)
def test_service_functions_are_security_definer_and_service_role_only(function: str) -> None:
    name = function.split("(")[0]
    definition = re.search(
        rf"create or replace function public\.{re.escape(name)}\((.*?)\) returns (\w+) (.*?) as \$\$", ALL_SQL
    )
    assert definition, f"{function} not defined with create or replace"
    attributes = definition.group(3)
    assert "security definer" in attributes
    assert "set search_path = public" in attributes
    assert f"revoke execute on function public.{function} from public, anon, authenticated;" in ALL_SQL
    grants = re.findall(rf"grant execute on function public\.{re.escape(function)} to ([\w, ]+);", ALL_SQL)
    assert grants == ["service_role"]


def test_promote_batch_retires_live_promotes_staged_and_deletes_retired() -> None:
    body = ALL_SQL.split("create or replace function public.promote_batch", 1)[1].split("$$;", 1)[0]
    delete_retired = body.find("delete from public.%i where publish_status = ''retired''")
    retire_live = body.find("set publish_status = ''retired'' where publish_status = ''live''")
    promote = body.find("set publish_status = ''live'' where batch_id = $1 and publish_status = ''staged''")
    assert 0 <= delete_retired < retire_live < promote
    for table in FACT_TABLES:
        assert f"'{table}'" in body
    assert "has no staged rows" in body  # an empty batch can never wipe live data


def test_discard_batch_deletes_only_staged_rows() -> None:
    body = ALL_SQL.split("create or replace function public.discard_batch", 1)[1].split("$$;", 1)[0]
    deletes = re.findall(r"delete from public\.%i where ([^']*''\w+'')", body)
    assert deletes == ["batch_id = $1 and publish_status = ''staged''"]


def test_fish_similarity_verified_defaults_to_false() -> None:
    assert "verified boolean not null default false" in ALL_SQL
    assert "check (similarity_score between 0 and 1)" in ALL_SQL
    assert "check (from_species_id <> to_species_id)" in ALL_SQL


def test_locations_level_check_contains_the_five_levels() -> None:
    match = re.search(r"level text not null check \(level in \(([^)]*)\)\)", ALL_SQL)
    assert match
    levels = {v.strip().strip("'") for v in match.group(1).split(",")}
    assert levels == {"pasar", "premis", "kawasan", "negeri", "nasional"}
    assert "check ((level = 'nasional') = (parent_id is null))" in ALL_SQL


def test_publish_status_is_constrained_on_every_fact_table() -> None:
    assert ALL_SQL.count("publish_status text not null check (publish_status in ('staged', 'live', 'retired'))") == 4


def test_enforce_consent_trigger_guards_user_preferences() -> None:
    sql = SQL["002_users_rls.sql"]
    assert "create or replace function public.enforce_consent() returns trigger" in sql
    assert "drop trigger if exists enforce_consent on public.user_preferences;" in sql
    assert (
        "create trigger enforce_consent before insert or update on public.user_preferences "
        "for each row execute function public.enforce_consent();" in sql
    )
    body = sql.split("create or replace function public.enforce_consent", 1)[1].split("$$;", 1)[0]
    for field in ("allergy_species_ids", "allergy_terms", "allergy_groups", "preferred_location_id"):
        assert f"new.{field}" in body
    assert "consent_allergies" in body and "consent_location" in body
    assert body.count("raise exception") == 2


def test_weather_forecasts_expires_at_is_not_null() -> None:
    assert "expires_at timestamptz not null" in SQL["003_future_tables.sql"]


def test_migrations_use_only_rerunnable_ddl() -> None:
    assert not re.search(r"create (?:unique )?index (?!if not exists)", ALL_SQL)
    assert not re.search(r"create function ", ALL_SQL)
    assert not re.search(r"\bdrop table\b", ALL_SQL)
    triggers = re.findall(r"create trigger (\w+) \w+ [^;]* on (public\.\w+)", ALL_SQL)
    for trigger, table in triggers:
        assert f"drop trigger if exists {trigger} on {table};" in ALL_SQL
