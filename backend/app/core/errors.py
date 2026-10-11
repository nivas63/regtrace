"""Application exception hierarchy.

Every error raised deliberately by RegTrace derives from `RegTraceError`. Each class
carries a stable machine-readable `code` and an HTTP status so the API layer can
translate it into an RFC 7807 problem document without per-route try/except.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar


class RegTraceError(Exception):
    code: ClassVar[str] = "internal_error"
    title: ClassVar[str] = "Internal error"
    http_status: ClassVar[int] = 500
    # Whether `detail` is safe to show to API clients.
    expose_detail: ClassVar[bool] = True

    def __init__(self, detail: str, *, context: Mapping[str, Any] | None = None) -> None:
        super().__init__(detail)
        self.detail = detail
        self.context: dict[str, Any] = dict(context or {})


class NotFoundError(RegTraceError):
    code = "not_found"
    title = "Resource not found"
    http_status = 404


class DomainValidationError(RegTraceError):
    code = "validation_failed"
    title = "Validation failed"
    http_status = 422


class ConflictError(RegTraceError):
    code = "conflict"
    title = "Conflict"
    http_status = 409


class AuthenticationError(RegTraceError):
    code = "unauthenticated"
    title = "Authentication required"
    http_status = 401


class AuthorizationError(RegTraceError):
    code = "forbidden"
    title = "Not permitted"
    http_status = 403


class DependencyUnavailableError(RegTraceError):
    """A required infrastructure dependency (database, object store) is unreachable."""

    code = "dependency_unavailable"
    title = "Dependency unavailable"
    http_status = 503
