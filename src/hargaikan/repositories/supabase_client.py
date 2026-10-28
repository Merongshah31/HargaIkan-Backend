"""Supabase client factories.

* :func:`service_role_client` — offline scripts and notebooks only (publishing). Never used by
  ``app.py``, ``pages/`` or ``services/``.
* :func:`anon_client` — the public anon key, for the app's read paths.

``supabase`` is imported lazily so importing this module never needs the package or a network.
No Streamlit import here: settings come from :func:`hargaikan.config.load_settings`. Error
messages name the missing key, never a value.
"""

from __future__ import annotations

from typing import Any

from hargaikan.config import (
    SUPABASE_ANON_KEY,
    SUPABASE_SERVICE_ROLE_KEY,
    SUPABASE_URL,
    Settings,
    load_settings,
)


class MissingSecretError(RuntimeError):
    """A required secret is not configured. ``names`` lists the missing keys (never values)."""

    def __init__(self, names: list[str]) -> None:
        self.names = tuple(names)
        super().__init__(f"missing required secret(s): {', '.join(names)}")


def _require(pairs: list[tuple[str, str | None]]) -> None:
    missing = [name for name, value in pairs if not value]
    if missing:
        raise MissingSecretError(missing)


def _create(url: str, key: str) -> Any:
    from supabase import create_client  # lazy: keeps imports cheap and offline-safe

    return create_client(url, key)


def service_role_client(settings: Settings | None = None) -> Any:
    """A supabase Client authenticated with the service role key (bypasses RLS).

    Raises MissingSecretError naming SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY if absent.
    """
    s = settings or load_settings()
    _require([(SUPABASE_URL, s.supabase_url), (SUPABASE_SERVICE_ROLE_KEY, s.supabase_service_role_key)])
    return _create(s.supabase_url, s.supabase_service_role_key)  # type: ignore[arg-type]


def anon_client(settings: Settings | None = None) -> Any:
    """A supabase Client with the public anon key (RLS applies).

    Raises MissingSecretError naming SUPABASE_URL / SUPABASE_ANON_KEY if absent.
    """
    s = settings or load_settings()
    _require([(SUPABASE_URL, s.supabase_url), (SUPABASE_ANON_KEY, s.supabase_anon_key)])
    return _create(s.supabase_url, s.supabase_anon_key)  # type: ignore[arg-type]


def safe_error(exc: BaseException) -> str:
    """Describe an exception by type and error code only, so no URL or key can leak."""
    code = getattr(exc, "code", None)
    return f"{type(exc).__name__}" + (f" (code {code})" if code else "")
