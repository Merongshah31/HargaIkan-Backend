"""Check the live Supabase schema against the expected 16-table schema (Requirement 2.10).

Usage:
    D:\\hargaikan-venv\\Scripts\\python.exe scripts/check_schema.py

Reads SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY via hargaikan.config.get_secret (a .env file in
the project root is loaded first if present). Calls the service-role-only RPC
``public.schema_report()`` from migration 001, because the Supabase REST API cannot query
information_schema directly.

Exit status: 0 = schema OK, 1 = problems found (or the RPC failed), 2 = a required secret is missing.
Secret values are never printed.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from hargaikan.config import SUPABASE_SERVICE_ROLE_KEY, SUPABASE_URL, get_secret
from hargaikan.pipeline.schema_check import (
    EXPECTED_SCHEMA,
    Problem,
    compare_schema,
    exit_code,
    parse_schema_report,
)

ROOT = Path(__file__).resolve().parents[1]
REQUIRED_SECRETS = (SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)


def load_dotenv_if_present(path: Path = ROOT / ".env") -> None:
    """Load KEY=VALUE pairs from .env without overriding variables already set."""
    if not path.is_file():
        return
    try:
        from dotenv import load_dotenv
    except ImportError:  # python-dotenv is in requirements.txt; tolerate its absence
        return
    load_dotenv(path, override=False)


def safe_error(exc: BaseException) -> str:
    """Describe an exception by type and error code only, so no URL or key can leak."""
    code = getattr(exc, "code", None)
    return f"{type(exc).__name__}" + (f" (code {code})" if code else "")


def collect_problems(client: Any) -> list[Problem]:
    """Fetch schema_report() through ``client`` (a service-role supabase Client) and compare."""
    response = client.rpc("schema_report").execute()
    actual_columns, rls = parse_schema_report(response.data)
    return compare_schema(EXPECTED_SCHEMA, actual_columns, rls)


def main(argv: list[str] | None = None) -> int:
    load_dotenv_if_present()
    missing = [name for name in REQUIRED_SECRETS if not get_secret(name)]
    if missing:
        print(f"check_schema: missing required secret(s): {', '.join(missing)}", file=sys.stderr)
        return 2

    from supabase import create_client  # imported late so a missing key exits before any client setup

    try:
        client = create_client(get_secret(SUPABASE_URL), get_secret(SUPABASE_SERVICE_ROLE_KEY))
        problems = collect_problems(client)
    except Exception as exc:  # network/API error, or schema_report() not installed yet
        print(
            f"check_schema: could not read the schema via rpc schema_report ({safe_error(exc)}). "
            "Run sql/migrations/001_catalog_prices.sql, 002_users_rls.sql and 003_future_tables.sql "
            "in the Supabase SQL editor first.",
            file=sys.stderr,
        )
        return 1

    if problems:
        print(f"check_schema: {len(problems)} problem(s) found:")
        for problem in problems:
            print(f"  - {problem.describe()}")
    else:
        print(f"check_schema: OK ({len(EXPECTED_SCHEMA)} tables, all columns present, RLS enabled)")
    return exit_code(problems)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
