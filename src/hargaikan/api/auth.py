"""Bearer-token authentication for the ``/api/v1/me*`` routes.

* ``memory`` backend: demo tokens ``demo.<uuid>.<sig>`` issued by ``POST /api/v1/session/demo``.
  ``sig`` is an HMAC-SHA256 (base64url) over ``demo.<uuid>`` with ``DEMO_SESSION_SECRET`` or a
  random per-process secret (tokens then stop working after a restart, like the memory store).
* ``supabase`` backend: the React app signs in with supabase-js ``signInAnonymously()`` and sends
  its access token. With ``SUPABASE_JWT_SECRET`` set and an HS256 token, the token is verified
  locally (signature, ``exp``, audience "authenticated"). Otherwise (no secret, or asymmetric
  signing keys) the token is checked with Supabase Auth ``GET /auth/v1/user``; successful checks
  are cached for ``Settings.auth_cache_ttl_s`` (never beyond the token's ``exp``) keyed by
  sha256(token). The user id is the token's ``sub``.

Tokens and secrets are never logged or echoed in errors.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import secrets
import threading
import time
import uuid
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

import httpx
import jwt

from hargaikan.config import DataBackend, Settings
from hargaikan.domain import messages as m
from hargaikan.services.result import ServiceUnavailable

logger = logging.getLogger(__name__)

DEMO_PREFIX = "demo"
AUDIENCE = "authenticated"
_MAX_CACHE = 4096


class AuthError(Exception):
    """Missing, malformed, expired or invalid token → HTTP 401 with AUTH_REQUIRED."""

    code = "auth_required"
    messages = (m.AUTH_REQUIRED,)


@dataclass(frozen=True)
class AuthUser:
    user_id: str
    access_token: str = field(repr=False)
    kind: Literal["demo", "supabase"]


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


class DemoTokens:
    def __init__(self, secret: str | None = None) -> None:
        self._key = secret.encode("utf-8") if secret else secrets.token_bytes(32)

    def _sig(self, uid: str) -> str:
        return _b64(hmac.new(self._key, f"{DEMO_PREFIX}.{uid}".encode(), hashlib.sha256).digest())

    def issue(self) -> tuple[str, str]:
        """(token, user_id) for a new demo session."""
        uid = str(uuid.uuid4())
        return f"{DEMO_PREFIX}.{uid}.{self._sig(uid)}", uid

    def verify(self, token: str) -> str:
        parts = token.split(".")
        if len(parts) != 3 or parts[0] != DEMO_PREFIX:
            raise AuthError()
        _, uid, sig = parts
        try:
            if str(uuid.UUID(uid)) != uid:
                raise ValueError
        except ValueError:
            raise AuthError() from None
        if not hmac.compare_digest(sig, self._sig(uid)):
            raise AuthError()
        return uid


class SupabaseTokenVerifier:
    def __init__(
        self,
        settings: Settings,
        http_client: httpx.Client | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._s = settings
        self._http = http_client
        self._clock = clock
        self._cache: OrderedDict[str, tuple[float, str]] = OrderedDict()  # sha256 → (expires_at, sub)
        self._lock = threading.Lock()

    def verify(self, token: str) -> str:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError:
            raise AuthError() from None
        secret = self._s.supabase_jwt_secret
        if secret and header.get("alg") == "HS256":
            return self._verify_local(token, secret)
        return self._verify_remote(token)

    @staticmethod
    def _verify_local(token: str, secret: str) -> str:
        try:
            claims = jwt.decode(
                token, secret, algorithms=["HS256"], audience=AUDIENCE, options={"require": ["exp", "sub"]}
            )
        except jwt.PyJWTError:
            raise AuthError() from None
        sub = claims.get("sub")
        if not isinstance(sub, str) or not sub:
            raise AuthError()
        return sub

    def _client(self) -> httpx.Client:
        if self._http is None:
            self._http = httpx.Client(timeout=self._s.service_timeout_s)
        return self._http

    def _verify_remote(self, token: str) -> str:
        key = hashlib.sha256(token.encode("utf-8")).hexdigest()
        now = self._clock()
        with self._lock:
            hit = self._cache.get(key)
            if hit is not None and hit[0] > now:
                return hit[1]
            self._cache.pop(key, None)
        try:
            exp = jwt.decode(token, options={"verify_signature": False}).get("exp")
        except jwt.PyJWTError:
            raise AuthError() from None
        if isinstance(exp, (int, float)) and exp <= now:
            raise AuthError()
        if not self._s.supabase_url or not self._s.supabase_anon_key:
            raise ServiceUnavailable("auth", m.PROFILE_UNAVAILABLE)
        url = f"{self._s.supabase_url.rstrip('/')}/auth/v1/user"
        try:
            resp = self._client().get(
                url,
                headers={"apikey": self._s.supabase_anon_key, "Authorization": f"Bearer {token}"},
                timeout=self._s.service_timeout_s,
            )
        except httpx.HTTPError as exc:
            logger.warning("Supabase Auth unreachable: %s", type(exc).__name__)
            raise ServiceUnavailable("auth", m.PROFILE_UNAVAILABLE) from None
        if 400 <= resp.status_code < 500:
            raise AuthError()
        if resp.status_code != 200:
            logger.warning("Supabase Auth returned HTTP %s", resp.status_code)
            raise ServiceUnavailable("auth", m.PROFILE_UNAVAILABLE)
        try:
            sub = resp.json().get("id")
        except ValueError:
            sub = None
        if not isinstance(sub, str) or not sub:
            raise AuthError()
        expires = now + self._s.auth_cache_ttl_s
        if isinstance(exp, (int, float)):
            expires = min(expires, float(exp))
        with self._lock:
            self._cache[key] = (expires, sub)
            while len(self._cache) > _MAX_CACHE:
                self._cache.popitem(last=False)
        return sub


class Authenticator:
    def __init__(self, backend: DataBackend, demo: DemoTokens, supabase: SupabaseTokenVerifier) -> None:
        self.backend = backend
        self.demo = demo
        self.supabase = supabase

    def authenticate(self, token: str | None) -> AuthUser:
        token = (token or "").strip()
        if not token:
            raise AuthError()
        if self.backend == "memory":
            return AuthUser(self.demo.verify(token), token, "demo")
        return AuthUser(self.supabase.verify(token), token, "supabase")
