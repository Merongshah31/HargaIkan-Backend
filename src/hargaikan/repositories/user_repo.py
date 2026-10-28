"""Supabase :class:`~hargaikan.repositories.protocols.UserRepository` bound to one user's token.

The client is created with the public anon key and every request carries the user's access
token (``Authorization: Bearer <token>``), so the owner-only RLS policies of migrations 002/003
apply (``user_id = auth.uid()``). The service role key is never used here.

Create one repository (and one client) per request/user: ``postgrest.auth`` mutates the
client's headers, so a client must never be shared between users. Tokens are never logged.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from hargaikan.config import Settings
from hargaikan.domain.models import UserPreferences, UserProfile
from hargaikan.repositories.rows import (
    USER_PREFERENCES_COLUMNS,
    USER_PROFILE_COLUMNS,
    user_preferences_from_row,
    user_preferences_row,
    user_profile_from_row,
    user_profile_row,
)

# delete_all order: children of user_profiles first, the profile row last.
USER_TABLES = ("user_preferences", "purchase_history", "travel_estimates", "user_profiles")


class SupabaseUserRepository:
    def __init__(self, client: Any, user_id: str, access_token: str) -> None:
        if not user_id:
            raise ValueError("user_id is required")
        if not access_token:
            raise ValueError("access_token is required")
        self.client = client
        self.user_id = str(user_id)
        self._token = access_token
        self.client.postgrest.auth(access_token)

    def __repr__(self) -> str:  # never show the token
        return f"SupabaseUserRepository(user_id={self.user_id!r})"

    @classmethod
    def from_settings(cls, settings: Settings, user_id: str, access_token: str) -> SupabaseUserRepository:
        """A repository with a fresh anon-key client (raises MissingSecretError if not configured)."""
        from hargaikan.repositories.supabase_client import anon_client

        return cls(anon_client(settings), user_id, access_token)

    def _table(self, name: str) -> Any:
        # Re-apply the user token before every request: supabase-py recreates its PostgREST client
        # on auth state events, which would otherwise fall back to the anon key.
        self.client.postgrest.auth(self._token)
        return self.client.table(name)

    def _check_owner(self, user_id: str) -> None:
        if str(user_id) != self.user_id:
            raise PermissionError("row belongs to another user")

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def get_profile(self) -> UserProfile | None:
        resp = self._table("user_profiles").select(USER_PROFILE_COLUMNS).eq("user_id", self.user_id).limit(1).execute()
        rows = resp.data or []
        return user_profile_from_row(rows[0]) if rows else None

    def upsert_profile(self, p: UserProfile) -> None:
        from postgrest.types import ReturnMethod

        self._check_owner(p.user_id)
        row = {**user_profile_row(p), "updated_at": self._now()}
        self._table("user_profiles").upsert(row, on_conflict="user_id", returning=ReturnMethod.minimal).execute()

    def get_preferences(self) -> UserPreferences | None:
        resp = (
            self._table("user_preferences").select(USER_PREFERENCES_COLUMNS).eq("user_id", self.user_id).limit(1).execute()
        )
        rows = resp.data or []
        return user_preferences_from_row(rows[0]) if rows else None

    def upsert_preferences(self, p: UserPreferences) -> None:
        """The enforce_consent trigger rejects allergy data / a preferred location without consent."""
        from postgrest.types import ReturnMethod

        self._check_owner(p.user_id)
        row = {**user_preferences_row(p), "updated_at": self._now()}
        self._table("user_preferences").upsert(row, on_conflict="user_id", returning=ReturnMethod.minimal).execute()

    def delete_all(self) -> None:
        """Delete the user's rows from every user table (RLS limits it to their own rows)."""
        from postgrest.types import ReturnMethod

        for table in USER_TABLES:
            self._table(table).delete(returning=ReturnMethod.minimal).eq("user_id", self.user_id).execute()
