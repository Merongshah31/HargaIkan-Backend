"""Typed service errors (layer 3). Services return plain values and raise these on failure.

The API layer maps them to HTTP responses (``{"detail": {"code": ..., "messages": [...]}}``):

* :class:`NotFoundError` → 404 (unknown species / location id)
* :class:`ValidationFailed` → 400 (consent, budget, travel field errors, ...)
* :class:`ServiceUnavailable` → 503 (Supabase or another outside service failed or timed out)

Every message is Bahasa Melayu text from :mod:`hargaikan.domain.messages`. Exception text never
contains secret values, tokens, URLs or user data.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from typing import TypeVar

from hargaikan.domain import messages as m
from hargaikan.repositories.supabase_client import safe_error

logger = logging.getLogger(__name__)
T = TypeVar("T")


class ServiceError(Exception):
    """Base class. ``code`` is a stable machine code, ``messages`` the BM texts for the user."""

    code = "service_error"

    def __init__(self, messages: tuple[str, ...]) -> None:
        self.messages = tuple(messages)
        super().__init__(self.code)


class NotFoundError(ServiceError):
    code = "not_found"

    def __init__(self, message: str = m.RESOURCE_NOT_FOUND) -> None:
        super().__init__((message,))


class ValidationFailed(ServiceError):
    """Rejected input. ``fields`` optionally maps an input field name to its BM error."""

    code = "validation_failed"

    def __init__(self, errors: tuple[str, ...], fields: Mapping[str, str] | None = None, code: str | None = None) -> None:
        super().__init__(tuple(errors))
        self.errors = self.messages
        self.fields = dict(fields or {})
        if code:
            self.code = code


class ServiceUnavailable(ServiceError):
    """An outside service failed for ``section``; only that section is unavailable."""

    code = "section_unavailable"

    def __init__(self, section: str, message: str = m.SECTION_UNAVAILABLE) -> None:
        super().__init__((message,))
        self.section = section
        self.message = message


def guarded(section: str, fn: Callable[[], T], message: str = m.SECTION_UNAVAILABLE) -> T:
    """Run ``fn``; any non-service exception becomes :class:`ServiceUnavailable` for ``section``.

    The cause is logged by exception type and error code only (no URLs, keys or user data).
    """
    try:
        return fn()
    except ServiceError:
        raise
    except Exception as exc:  # repository / network failure
        logger.warning("section %s unavailable: %s", section, safe_error(exc))
        raise ServiceUnavailable(section, message) from exc
