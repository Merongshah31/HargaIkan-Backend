"""Property and unit tests for hargaikan.core.profile_rules (Properties 33 and 34)."""

from __future__ import annotations

import math
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from hargaikan.config import Settings
from hargaikan.core.profile_rules import (
    BudgetError,
    ProfileForm,
    apply_consent_withdrawal,
    parse_budget,
    validate_profile,
)
from hargaikan.domain import messages as m
from hargaikan.domain.models import UserPreferences

SPECIES = ("s1", "s2", "s3")
LOCATIONS = ("ck", "kl")
S = Settings()
MAX = S.max_budget_rm_per_kg

blankish = st.sampled_from(["", " ", "  \t"])
id_lists = st.lists(st.sampled_from(SPECIES) | blankish, max_size=3).map(tuple)
text_lists = st.lists(st.sampled_from(["udang", "  ketam ", "sotong"]) | blankish, max_size=3).map(tuple)
group_lists = st.lists(st.sampled_from(["krustasea", "moluska"]) | blankish, max_size=2).map(tuple)
valid_budgets = st.none() | blankish | st.floats(min_value=0.01, max_value=MAX) | st.sampled_from(["12.50", "RM8", "1,000"])


@st.composite
def forms(draw: st.DrawFn, budget=valid_budgets) -> ProfileForm:  # type: ignore[no-untyped-def]
    return ProfileForm(
        preferred_species_ids=draw(id_lists),
        allergy_species_ids=draw(id_lists),
        allergy_terms=draw(text_lists),
        allergy_groups=draw(group_lists),
        budget=draw(budget),
        preferred_location_id=draw(st.none() | blankish | st.sampled_from(LOCATIONS)),
        consent_allergies=draw(st.booleans()),
        consent_location=draw(st.booleans()),
    )


class FakeStore:
    """Minimal user store mirroring how ProfileService.save uses validate_profile."""

    def __init__(self, prefs: UserPreferences | None) -> None:
        self.prefs = prefs

    def save(self, form: ProfileForm) -> bool:
        v = validate_profile(form, S, SPECIES, LOCATIONS, user_id="u1")
        if v.ok:
            self.prefs = v.preferences
        return v.ok


def _nonblank(xs: tuple[str, ...]) -> bool:
    return any(x.strip() for x in xs)


# Feature: hargaikan-v2, Property 33: Consent gates sensitive fields
@given(form=forms(), consent_a=st.booleans(), consent_l=st.booleans())
def test_consent_gates_sensitive_fields(form: ProfileForm, consent_a: bool, consent_l: bool) -> None:
    """*For any* profile form and consent state, a save with non-empty allergy data and allergy consent
    false, or with a preferred location and location consent false, is rejected and leaves the fake
    user store unchanged. Withdrawing a consent leaves the matching stored fields empty.

    **Validates: Requirements 14.2, 14.3, 14.4**
    """
    before = UserPreferences(user_id="u1", preferred_species_ids=("s3",), allergy_terms=("lama",),
                             budget_rm_per_kg=7.0, preferred_location_id="kl")
    store = FakeStore(before)
    has_allergy = _nonblank(form.allergy_species_ids) or _nonblank(form.allergy_terms) or _nonblank(form.allergy_groups)
    has_location = bool(form.preferred_location_id and form.preferred_location_id.strip())
    violates = (has_allergy and not form.consent_allergies) or (has_location and not form.consent_location)

    v = validate_profile(form, S, SPECIES, LOCATIONS, user_id="u1")
    ok = store.save(form)
    assert ok == v.ok == (not violates)  # every other field in these forms is valid
    if violates:
        assert store.prefs == before
        assert v.preferences is None
        if has_allergy and not form.consent_allergies:
            assert m.CONSENT_REQUIRED_ALLERGY in v.errors
        if has_location and not form.consent_location:
            assert m.CONSENT_REQUIRED_LOCATION in v.errors
    else:
        assert v.errors == () and store.prefs == v.preferences
        p = v.preferences
        assert p is not None
        if not form.consent_allergies:
            assert (p.allergy_species_ids, p.allergy_terms, p.allergy_groups) == ((), (), ())
        if not form.consent_location:
            assert p.preferred_location_id is None

    # Withdrawal clears exactly the matching fields.
    stored = store.prefs
    assert stored is not None
    after = apply_consent_withdrawal(stored, consent_a, consent_l)
    if not consent_a:
        assert (after.allergy_species_ids, after.allergy_terms, after.allergy_groups) == ((), (), ())
    else:
        assert (after.allergy_species_ids, after.allergy_terms, after.allergy_groups) == (
            stored.allergy_species_ids, stored.allergy_terms, stored.allergy_groups)
    if not consent_l:
        assert after.preferred_location_id is None
    else:
        assert after.preferred_location_id == stored.preferred_location_id
    assert (after.user_id, after.preferred_species_ids, after.budget_rm_per_kg) == (
        stored.user_id, stored.preferred_species_ids, stored.budget_rm_per_kg)


budget_inputs = st.one_of(
    st.none(),
    blankish,
    st.booleans(),
    st.floats(allow_nan=True, allow_infinity=True),
    st.floats(min_value=-10, max_value=MAX * 2),
    st.integers(min_value=-10, max_value=int(MAX) * 2),
    st.decimals(allow_nan=True, allow_infinity=True, places=2),
    st.floats(min_value=-10, max_value=MAX * 2).map(lambda x: f"{x:.2f}"),
    st.sampled_from(["abc", "RM", "nan", "inf", "-5", "0", "RM0", "1000", "1000.01", "RM 12.50", "1,000.00"]),
    st.lists(st.integers(), max_size=2),
)


def _oracle_budget(raw: object) -> tuple[str, float | None]:
    """('none' | 'ok' | 'error', value)."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return "none", None
    if isinstance(raw, bool):
        return "error", None
    try:
        if isinstance(raw, (int, float, Decimal)):
            b = float(raw)
        else:
            b = float(str(raw).upper().replace("RM", "").replace(",", "").strip())
    except ValueError:  # non-numeric text, or a signalling Decimal NaN
        return "error", None
    return ("ok", b) if math.isfinite(b) and 0 < b <= MAX else ("error", None)


# Feature: hargaikan-v2, Property 34: Budget validation
@given(raw=budget_inputs)
def test_budget_validation(raw: object) -> None:
    """*For any* input value, the budget is accepted if and only if it parses to a finite number b with
    0 < b ≤ max_budget_rm_per_kg. A rejected budget persists nothing from that save.

    **Validates: Requirements 14.5**
    """
    kind, value = _oracle_budget(raw)
    before = UserPreferences(user_id="u1", preferred_species_ids=("s1",), budget_rm_per_kg=5.0)
    store = FakeStore(before)
    form = ProfileForm(preferred_species_ids=("s2",), budget=raw)
    if kind == "error":
        with pytest.raises(BudgetError) as exc:
            parse_budget(raw, MAX)
        assert str(exc.value) == "Bajet mesti nombor antara RM0.01 dan RM1000/kg."
        assert store.save(form) is False
        assert store.prefs == before
    else:
        assert parse_budget(raw, MAX) == value
        assert store.save(form) is True
        assert store.prefs is not None and store.prefs.budget_rm_per_kg == value


# ------------------------------------------------------------------------------------ examples


def test_valid_save_cleans_and_dedupes() -> None:
    form = ProfileForm(preferred_species_ids=("s1", " s1 ", "", "s2"), allergy_terms=(" udang ", "", "udang"),
                       budget="RM12.50", preferred_location_id=" ck ", consent_allergies=True, consent_location=True)
    v = validate_profile(form, S, SPECIES, LOCATIONS, user_id="u9")
    assert v.ok and v.errors == ()
    assert v.preferences == UserPreferences(user_id="u9", preferred_species_ids=("s1", "s2"), allergy_terms=("udang",),
                                            budget_rm_per_kg=12.5, preferred_location_id="ck")


def test_unknown_ids_and_all_errors_reported_together() -> None:
    form = ProfileForm(preferred_species_ids=("nope",), allergy_groups=("krustasea",), budget="-1",
                       preferred_location_id="mars")
    v = validate_profile(form, S, SPECIES, LOCATIONS)
    assert not v.ok and v.preferences is None
    assert v.errors == (m.CONSENT_REQUIRED_ALLERGY, m.CONSENT_REQUIRED_LOCATION,
                        "Bajet mesti nombor antara RM0.01 dan RM1000/kg.", m.UNKNOWN_SPECIES, m.UNKNOWN_LOCATION)


def test_empty_form_is_valid() -> None:
    v = validate_profile(ProfileForm(), S, SPECIES, LOCATIONS)
    assert v.ok and v.preferences == UserPreferences(user_id="")


def test_budget_message_formats_non_integer_max() -> None:
    with pytest.raises(BudgetError, match=r"RM250\.50/kg"):
        parse_budget("300", 250.5)
