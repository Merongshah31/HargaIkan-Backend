"""Profile validation rules: budget parsing, consent gating, known ids and consent withdrawal.

Pure logic (layer 1): no I/O and no supabase, pinecone, requests, streamlit or fastapi imports.
The ProfileService / profile API calls :func:`validate_profile` before writing anything, so a
rejected save persists nothing (Requirement 14.2, 14.3, 14.5).
"""

from __future__ import annotations

import math
from collections.abc import Collection, Iterable
from dataclasses import dataclass, replace

from hargaikan.config import Settings
from hargaikan.domain import messages as m
from hargaikan.domain.models import UserPreferences


class BudgetError(ValueError):
    """Raised by :func:`parse_budget` for a budget that is not a finite number in (0, max]."""


@dataclass(frozen=True)
class ProfileForm:
    """Raw profile input as submitted by the user (before validation)."""

    preferred_species_ids: tuple[str, ...] = ()
    allergy_species_ids: tuple[str, ...] = ()
    allergy_terms: tuple[str, ...] = ()
    allergy_groups: tuple[str, ...] = ()
    budget: object = None  # raw user input, e.g. "12.50", 12.5, "" or None
    preferred_location_id: str | None = None
    consent_allergies: bool = False
    consent_location: bool = False


@dataclass(frozen=True)
class ProfileValidation:
    ok: bool
    preferences: UserPreferences | None  # set only when ok
    errors: tuple[str, ...]  # Bahasa Melayu messages, empty when ok


def format_rm(x: float) -> str:
    """``1000.0`` → ``"1000"``, ``12.5`` → ``"12.50"`` (used in BUDGET_INVALID)."""
    return str(int(x)) if math.isfinite(x) and x == int(x) else f"{x:.2f}"


def budget_error_text(max_budget: float) -> str:
    return m.BUDGET_INVALID.format(max=format_rm(max_budget))


def parse_budget(raw: object, max_budget: float) -> float | None:
    """Parse an optional budget in RM/kg.

    None or blank text → None (no budget). Otherwise the value (optionally written with "RM" and
    thousands separators) must be a finite number b with 0 < b ≤ ``max_budget``; anything else,
    including booleans, raises BudgetError carrying the BUDGET_INVALID text.
    """
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    if isinstance(raw, bool):
        raise BudgetError(budget_error_text(max_budget))
    try:
        b = float(str(raw).upper().replace("RM", "").replace(",", "").strip())
    except (ValueError, TypeError, OverflowError):
        raise BudgetError(budget_error_text(max_budget)) from None
    if not math.isfinite(b) or b <= 0 or b > max_budget:
        raise BudgetError(budget_error_text(max_budget))
    return b


def _clean_ids(values: Iterable[str]) -> tuple[str, ...]:
    """Strip, drop blanks and de-duplicate, keeping first-seen order."""
    out: dict[str, None] = {}
    for v in values:
        t = str(v).strip()
        if t:
            out.setdefault(t, None)
    return tuple(out)


def has_allergy_data(form: ProfileForm) -> bool:
    return bool(_clean_ids(form.allergy_species_ids) or _clean_ids(form.allergy_terms) or _clean_ids(form.allergy_groups))


def _clean_location(value: str | None) -> str | None:
    if value is None:
        return None
    t = str(value).strip()
    return t or None


def validate_profile(
    form: ProfileForm,
    s: Settings,
    known_species_ids: Collection[str],
    known_location_ids: Collection[str],
    *,
    user_id: str = "",
) -> ProfileValidation:
    """Validate a whole profile save. Any error rejects the entire save (no partial writes).

    Checks, all reported together: allergy data without allergy consent (CONSENT_REQUIRED_ALLERGY),
    a preferred location without location consent (CONSENT_REQUIRED_LOCATION), an invalid budget
    (BUDGET_INVALID), unknown species ids (UNKNOWN_SPECIES) and an unknown location id
    (UNKNOWN_LOCATION). Ids are stripped and de-duplicated; blank entries are dropped.
    """
    preferred = _clean_ids(form.preferred_species_ids)
    allergy_ids = _clean_ids(form.allergy_species_ids)
    terms = _clean_ids(form.allergy_terms)
    groups = _clean_ids(form.allergy_groups)
    location = _clean_location(form.preferred_location_id)

    errors: list[str] = []
    if (allergy_ids or terms or groups) and not form.consent_allergies:
        errors.append(m.CONSENT_REQUIRED_ALLERGY)
    if location is not None and not form.consent_location:
        errors.append(m.CONSENT_REQUIRED_LOCATION)
    budget: float | None = None
    try:
        budget = parse_budget(form.budget, s.max_budget_rm_per_kg)
    except BudgetError as exc:
        errors.append(str(exc))
    known_species = set(known_species_ids)
    if any(sid not in known_species for sid in (*preferred, *allergy_ids)):
        errors.append(m.UNKNOWN_SPECIES)
    if location is not None and location not in set(known_location_ids):
        errors.append(m.UNKNOWN_LOCATION)

    if errors:
        return ProfileValidation(False, None, tuple(errors))
    prefs = UserPreferences(
        user_id=user_id,
        preferred_species_ids=preferred,
        allergy_species_ids=allergy_ids,
        allergy_terms=terms,
        allergy_groups=groups,
        budget_rm_per_kg=budget,
        preferred_location_id=location,
    )
    return ProfileValidation(True, prefs, ())


def apply_consent_withdrawal(prefs: UserPreferences, consent_allergies: bool, consent_location: bool) -> UserPreferences:
    """Clear fields whose consent is not (or no longer) given.

    No allergy consent → allergy species ids, terms and groups are emptied. No location consent →
    preferred_location_id is None. Other fields are unchanged.
    """
    out = prefs
    if not consent_allergies:
        out = replace(out, allergy_species_ids=(), allergy_terms=(), allergy_groups=())
    if not consent_location:
        out = replace(out, preferred_location_id=None)
    return out
