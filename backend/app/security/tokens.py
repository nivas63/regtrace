"""JWT access tokens (HS256). OIDC-ready: claims mirror what an IdP would issue."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt

from app.config import Settings
from app.core.errors import AuthenticationError

_ALGORITHM = "HS256"
_ISSUER = "regtrace"


@dataclass(frozen=True)
class Principal:
    user_id: uuid.UUID
    bank_id: uuid.UUID
    role: str
    email: str


def issue_token(settings: Settings, principal: Principal) -> str:
    now = datetime.now(UTC)
    claims = {
        "sub": str(principal.user_id),
        "bank_id": str(principal.bank_id),
        "role": principal.role,
        "email": principal.email,
        "iss": _ISSUER,
        "iat": now,
        "exp": now + timedelta(minutes=settings.jwt_ttl_minutes),
    }
    return jwt.encode(claims, settings.jwt_secret.get_secret_value(), algorithm=_ALGORITHM)


def decode_token(settings: Settings, token: str) -> Principal:
    try:
        claims = jwt.decode(
            token,
            settings.jwt_secret.get_secret_value(),
            algorithms=[_ALGORITHM],
            issuer=_ISSUER,
            options={"require": ["sub", "bank_id", "role", "exp"]},
        )
        return Principal(
            user_id=uuid.UUID(claims["sub"]),
            bank_id=uuid.UUID(claims["bank_id"]),
            role=str(claims["role"]),
            email=str(claims.get("email", "")),
        )
    except (jwt.PyJWTError, ValueError, KeyError) as exc:
        raise AuthenticationError("Invalid or expired token") from exc
