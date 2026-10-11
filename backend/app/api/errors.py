"""Translate exceptions into RFC 7807 `application/problem+json` responses."""

from __future__ import annotations

from typing import Any, cast

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.errors import RegTraceError
from app.observability.logging import get_logger

PROBLEM_JSON = "application/problem+json"
_log = get_logger("regtrace.api.errors")


def _problem(
    request: Request,
    *,
    status: int,
    code: str,
    title: str,
    detail: str | None,
    extra: dict[str, Any] | None = None,
) -> JSONResponse:
    body: dict[str, Any] = {
        "type": f"https://regtrace.dev/problems/{code}",
        "title": title,
        "status": status,
        "code": code,
        "instance": request.url.path,
        "request_id": getattr(request.state, "request_id", None),
    }
    if detail is not None:
        body["detail"] = detail
    if extra:
        body.update(extra)
    return JSONResponse(body, status_code=status, media_type=PROBLEM_JSON)


async def _regtrace_error(request: Request, exc: Exception) -> JSONResponse:
    exc = cast(RegTraceError, exc)
    log = _log.error if exc.http_status >= 500 else _log.info
    log("request.failed", code=exc.code, detail=exc.detail, context=exc.context)
    return _problem(
        request,
        status=exc.http_status,
        code=exc.code,
        title=exc.title,
        detail=exc.detail if exc.expose_detail else None,
    )


async def _validation_error(request: Request, exc: Exception) -> JSONResponse:
    exc = cast(RequestValidationError, exc)
    errors = [{"loc": list(err["loc"]), "msg": err["msg"], "type": err["type"]} for err in exc.errors()]
    return _problem(
        request,
        status=422,
        code="request_validation_failed",
        title="Request validation failed",
        detail="The request body or parameters are invalid.",
        extra={"errors": errors},
    )


async def _http_error(request: Request, exc: Exception) -> JSONResponse:
    exc = cast(StarletteHTTPException, exc)
    return _problem(
        request,
        status=exc.status_code,
        code="http_error",
        title=str(exc.detail),
        detail=None,
    )


async def _unhandled_error(request: Request, exc: Exception) -> JSONResponse:
    # Full traceback goes to logs; the client gets no internals.
    _log.exception("request.unhandled", error_type=type(exc).__name__)
    return _problem(
        request,
        status=500,
        code="internal_error",
        title="Internal error",
        detail="An unexpected error occurred. Quote the request_id when reporting it.",
    )


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(RegTraceError, _regtrace_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(StarletteHTTPException, _http_error)
    app.add_exception_handler(Exception, _unhandled_error)
