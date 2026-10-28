"""Authenticated ``/api/v1/me*`` routes: profile, consent, delete-all and the dashboard.

Every handler is bound to the caller's own user id (demo token or Supabase access token, so RLS
applies). User data is never cached or logged.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from hargaikan.api import presenters as p
from hargaikan.api import schemas as s
from hargaikan.api.auth import AuthUser
from hargaikan.api.container import AppContainer
from hargaikan.api.deps import current_user, get_container
from hargaikan.core.profile_rules import ProfileForm
from hargaikan.domain import messages as m

router = APIRouter(prefix="/me", tags=["me"], responses={code: {"model": s.ErrorResponse} for code in (400, 401, 404, 422, 503)})


@router.get("", response_model=s.MeOut)
def me(user: AuthUser = Depends(current_user), c: AppContainer = Depends(get_container)) -> s.MeOut:
    return s.MeOut(user_id=user.user_id, backend=c.backend, demo=c.demo, session_notice=m.SESSION_NOTICE)


@router.get("/profile", response_model=s.ProfileOut)
def get_profile(user: AuthUser = Depends(current_user), c: AppContainer = Depends(get_container)) -> s.ProfileOut:
    return p.profile(c.profile_service(user).load())


@router.put("/profile", response_model=s.ProfileOut)
def put_profile(
    body: s.ProfileIn, user: AuthUser = Depends(current_user), c: AppContainer = Depends(get_container)
) -> s.ProfileOut:
    form = ProfileForm(
        preferred_species_ids=tuple(body.preferred_species_ids),
        allergy_species_ids=tuple(body.allergy_species_ids),
        allergy_terms=tuple(body.allergy_terms),
        allergy_groups=tuple(body.allergy_groups),
        budget=body.budget_rm_per_kg,
        preferred_location_id=body.preferred_location_id,
        consent_allergies=body.consent_allergies,
        consent_location=body.consent_location,
    )
    return p.profile(c.profile_service(user).save(form), m.PROFILE_SAVED)


@router.post("/consent/withdraw", response_model=s.ProfileOut)
def withdraw_consent(
    body: s.ConsentWithdrawIn, user: AuthUser = Depends(current_user), c: AppContainer = Depends(get_container)
) -> s.ProfileOut:
    state = c.profile_service(user).withdraw_consent(body.kind)
    msg = m.CONSENT_WITHDRAWN_ALLERGIES if body.kind == "allergies" else m.CONSENT_WITHDRAWN_LOCATION
    return p.profile(state, msg)


@router.delete("", response_model=s.DeletedOut)
def delete_me(user: AuthUser = Depends(current_user), c: AppContainer = Depends(get_container)) -> s.DeletedOut:
    """Delete every row of the caller in the user tables. The client then discards its token
    (supabase-js ``signOut()``)."""
    c.profile_service(user).delete_all()
    return s.DeletedOut(deleted=True, message=m.DATA_DELETED)


@router.get("/dashboard", response_model=s.DashboardOut)
def dashboard(user: AuthUser = Depends(current_user), c: AppContainer = Depends(get_container)) -> s.DashboardOut:
    prefs = c.profile_service(user).preferences()
    return p.dashboard(c.services().dashboard.for_user(prefs, c.today()))
