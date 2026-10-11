"""HTTP helpers shared by provider adapters: one place that maps transport and
status-code failures onto the router's error taxonomy."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx

from app.llm.errors import (
    LLMPermanentError,
    LLMProviderError,
    LLMRateLimitedError,
    LLMTransientError,
)

_TRANSIENT_STATUS = frozenset({408, 409, 500, 502, 503, 504, 529})
_MAX_ERROR_BODY = 500


def parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None  # HTTP-date form is not used by these providers


async def post_json(
    client: httpx.AsyncClient,
    *,
    provider: str,
    url: str,
    headers: dict[str, str],
    body: dict[str, Any],
    timeout_s: float,
    retry_after_from_body: Callable[[dict[str, Any]], float | None] | None = None,
) -> dict[str, Any]:
    try:
        response = await client.post(url, headers=headers, json=body, timeout=timeout_s)
    except httpx.TimeoutException as exc:
        raise LLMTransientError(f"{provider} request timed out", provider=provider) from exc
    except httpx.TransportError as exc:
        raise LLMTransientError(f"{provider} transport error: {type(exc).__name__}", provider=provider) from exc
    return _decode(response, provider, retry_after_from_body)


async def get_json(
    client: httpx.AsyncClient,
    *,
    provider: str,
    url: str,
    headers: dict[str, str],
    timeout_s: float,
) -> dict[str, Any]:
    try:
        response = await client.get(url, headers=headers, timeout=timeout_s)
    except httpx.HTTPError as exc:
        raise LLMTransientError(f"{provider} transport error: {type(exc).__name__}", provider=provider) from exc
    return _decode(response, provider, None)


def _decode(
    response: httpx.Response,
    provider: str,
    retry_after_from_body: Callable[[dict[str, Any]], float | None] | None,
) -> dict[str, Any]:
    status = response.status_code
    payload: dict[str, Any] = {}
    try:
        decoded = response.json()
        if isinstance(decoded, dict):
            payload = decoded
    except ValueError:
        pass

    if status < 400:
        if not payload:
            raise LLMTransientError(f"{provider} returned a non-JSON body", provider=provider)
        return payload

    snippet = response.text[:_MAX_ERROR_BODY]
    detail = f"{provider} HTTP {status}: {snippet}"
    error: LLMProviderError
    if status == 429:
        retry_after = parse_retry_after(response.headers.get("retry-after"))
        if retry_after is None and retry_after_from_body is not None:
            retry_after = retry_after_from_body(payload)
        error = LLMRateLimitedError(detail, provider=provider, retry_after_s=retry_after)
    elif status in _TRANSIENT_STATUS:
        error = LLMTransientError(detail, provider=provider, status_code=status)
    else:
        error = LLMPermanentError(detail, provider=provider, status_code=status)
    raise error
