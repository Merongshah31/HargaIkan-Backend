"""Exception → JSON mapping: ``{"detail": {"code": ..., "messages": [BM text, ...]}}``."""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from hargaikan.api.auth import AuthError
from hargaikan.domain import messages as m
from hargaikan.services.result import NotFoundError, ServiceUnavailable, ValidationFailed

logger = logging.getLogger(__name__)


def error_body(code: str, messages: list[str] | tuple[str, ...], fields: dict[str, str] | None = None) -> dict:
    detail: dict = {"code": code, "messages": list(messages)}
    if fields:
        detail["fields"] = dict(fields)
    return {"detail": detail}


def _json(status: int, code: str, messages, fields=None, headers=None) -> JSONResponse:  # type: ignore[no-untyped-def]
    return JSONResponse(status_code=status, content=error_body(code, messages, fields), headers=headers)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AuthError)
    async def _auth(_: Request, exc: AuthError) -> JSONResponse:
        return _json(401, exc.code, exc.messages, headers={"WWW-Authenticate": "Bearer"})

    @app.exception_handler(NotFoundError)
    async def _not_found(_: Request, exc: NotFoundError) -> JSONResponse:
        return _json(404, exc.code, exc.messages)

    @app.exception_handler(ValidationFailed)
    async def _invalid(_: Request, exc: ValidationFailed) -> JSONResponse:
        return _json(400, exc.code, exc.messages, exc.fields or None)

    @app.exception_handler(ServiceUnavailable)
    async def _unavailable(_: Request, exc: ServiceUnavailable) -> JSONResponse:
        return _json(503, exc.code, exc.messages)

    @app.exception_handler(RequestValidationError)
    async def _request_invalid(_: Request, exc: RequestValidationError) -> JSONResponse:
        fields = {}
        for err in exc.errors():
            loc = [str(p) for p in err.get("loc", ()) if p not in ("body", "query", "path")]
            if loc:
                fields[".".join(loc)] = m.INVALID_REQUEST
        return _json(422, "invalid_request", [m.INVALID_REQUEST], fields or None)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        if exc.status_code == 404:
            return _json(404, "not_found", [m.RESOURCE_NOT_FOUND])
        if exc.status_code == 405:
            return _json(405, "method_not_allowed", [m.INVALID_REQUEST])
        return _json(exc.status_code, "http_error", [m.INVALID_REQUEST if exc.status_code < 500 else m.SECTION_UNAVAILABLE])

    @app.exception_handler(Exception)
    async def _unexpected(_: Request, exc: Exception) -> JSONResponse:
        logger.error("unhandled error: %s", type(exc).__name__)
        return _json(500, "internal_error", [m.SECTION_UNAVAILABLE])
