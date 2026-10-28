"""Profile service: load, save, withdraw consent and delete all user data (Requirement 14).

Layer 3 (no FastAPI imports). Bound to one :class:`UserRepository` (the caller's token / RLS).
User data is never cached and never logged.

Save order: validate everything first (nothing is written on any error) → write the profile
(consent flags, timestamps set when consent is newly granted) → write the preferences (so the
database ``enforce_consent`` trigger sees the consent). Consent turned off clears the matching
preference fields. A consent rejection from the repository (memory ``ConsentRequiredError`` or
Supabase ``check_violation`` ``consent_*_required``) becomes the BM consent message.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from hargaikan.config import Settings
from hargaikan.core.profile_rules import ProfileForm, apply_consent_withdrawal, validate_profile
from hargaikan.domain import messages as m
from hargaikan.domain.models import UserPreferences, UserProfile
from hargaikan.repositories.protocols import UserRepository
from hargaikan.services.catalog_service import CatalogService
from hargaikan.services.result import ServiceError, ServiceUnavailable, ValidationFailed, guarded

ConsentKind = Literal["allergies", "location"]
_CONSENT_CODES = {
    "consent_allergies_required": m.CONSENT_REQUIRED_ALLERGY,
    "consent_location_required": m.CONSENT_REQUIRED_LOCATION,
}


@dataclass(frozen=True)
class ProfileState:
    user_id: str
    profile: UserProfile | None
    preferences: UserPreferences | None


def consent_message(exc: BaseException) -> str | None:
    """The BM consent message for a repository consent rejection, else None."""
    texts = [str(getattr(exc, "code", "") or ""), str(getattr(exc, "message", "") or ""), str(exc)]
    for code, msg in _CONSENT_CODES.items():
        if any(code in t for t in texts):
            return msg
    return None


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ProfileService:
    def __init__(
        self,
        repo: UserRepository,
        user_id: str,
        catalog: CatalogService,
        settings: Settings,
        clock: Callable[[], datetime] = _now,
    ) -> None:
        self._repo = repo
        self._user_id = user_id
        self._catalog = catalog
        self._s = settings
        self._clock = clock

    def _call(self, fn: Callable[[], object]) -> object:
        return guarded("profile", fn, m.PROFILE_UNAVAILABLE)

    def _write(self, fn: Callable[[], None]) -> None:
        try:
            fn()
        except ServiceError:
            raise
        except Exception as exc:
            msg = consent_message(exc)
            if msg is not None:
                raise ValidationFailed((msg,), code="consent_required") from None
            raise ServiceUnavailable("profile", m.PROFILE_UNAVAILABLE) from exc

    def load(self) -> ProfileState:
        profile = self._call(self._repo.get_profile)
        prefs = self._call(self._repo.get_preferences)
        return ProfileState(self._user_id, profile, prefs)  # type: ignore[arg-type]

    def preferences(self) -> UserPreferences | None:
        return self._call(self._repo.get_preferences)  # type: ignore[return-value]

    def save(self, form: ProfileForm) -> ProfileState:
        snap = self._catalog.snapshot()
        v = validate_profile(
            form, self._s, snap.species_by_id.keys(), snap.locations_by_id.keys(), user_id=self._user_id
        )
        if not v.ok:
            consent = any(e in (m.CONSENT_REQUIRED_ALLERGY, m.CONSENT_REQUIRED_LOCATION) for e in v.errors)
            raise ValidationFailed(v.errors, code="consent_required" if consent else "validation_failed")
        assert v.preferences is not None

        existing: UserProfile | None = self._call(self._repo.get_profile)  # type: ignore[assignment]
        now = self._clock()

        def since(granted: bool, before: bool, at: datetime | None) -> datetime | None:
            if not granted:
                return None
            return at if before and at is not None else now

        profile = UserProfile(
            user_id=self._user_id,
            consent_allergies=form.consent_allergies,
            consent_location=form.consent_location,
            consent_allergies_at=since(
                form.consent_allergies, bool(existing and existing.consent_allergies), existing.consent_allergies_at if existing else None
            ),
            consent_location_at=since(
                form.consent_location, bool(existing and existing.consent_location), existing.consent_location_at if existing else None
            ),
        )
        prefs = apply_consent_withdrawal(v.preferences, form.consent_allergies, form.consent_location)
        self._write(lambda: self._repo.upsert_profile(profile))  # profile first: the trigger reads it
        self._write(lambda: self._repo.upsert_preferences(prefs))
        return ProfileState(self._user_id, profile, prefs)

    def withdraw_consent(self, kind: ConsentKind) -> ProfileState:
        """Turn off one consent and delete the matching stored data (allergies or location)."""
        if kind not in ("allergies", "location"):
            raise ValidationFailed((m.INVALID_REQUEST,))
        profile: UserProfile | None = self._call(self._repo.get_profile)  # type: ignore[assignment]
        prefs: UserPreferences | None = self._call(self._repo.get_preferences)  # type: ignore[assignment]
        allergies = bool(profile and profile.consent_allergies) and kind != "allergies"
        location = bool(profile and profile.consent_location) and kind != "location"
        if prefs is not None:  # delete the data first, then record the withdrawn consent
            cleared = apply_consent_withdrawal(prefs, allergies, location)
            self._write(lambda: self._repo.upsert_preferences(cleared))
            prefs = cleared
        if profile is not None:
            profile = UserProfile(
                user_id=self._user_id,
                consent_allergies=allergies,
                consent_location=location,
                consent_allergies_at=profile.consent_allergies_at if allergies else None,
                consent_location_at=profile.consent_location_at if location else None,
            )
            self._write(lambda: self._repo.upsert_profile(profile))
        return ProfileState(self._user_id, profile, prefs)

    def delete_all(self) -> None:
        self._call(self._repo.delete_all)
