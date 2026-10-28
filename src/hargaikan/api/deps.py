"""FastAPI dependencies: the app container and bearer-token users."""

from __future__ import annotations

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from hargaikan.api.auth import AuthError, AuthUser
from hargaikan.api.container import AppContainer
from hargaikan.services.result import ServiceUnavailable

bearer = HTTPBearer(auto_error=False, description="Demo token (memory backend) or Supabase access token")


def get_container(request: Request) -> AppContainer:
    return request.app.state.container


def current_user(
    container: AppContainer = Depends(get_container),
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> AuthUser:
    """The authenticated caller; 401 AUTH_REQUIRED without a valid bearer token."""
    if creds is None or creds.scheme.lower() != "bearer":
        raise AuthError()
    return container.authenticator.authenticate(creds.credentials)


def optional_user(
    container: AppContainer = Depends(get_container),
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> AuthUser | None:
    """The caller if a valid token was sent, else None (public result)."""
    if creds is None or creds.scheme.lower() != "bearer":
        return None
    try:
        return container.authenticator.authenticate(creds.credentials)
    except (AuthError, ServiceUnavailable):
        return None
