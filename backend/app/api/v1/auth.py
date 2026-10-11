from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import PrincipalDep, ServicesDep
from app.core.errors import AuthenticationError
from app.models import Bank, User
from app.security.passwords import verify_password
from app.security.tokens import Principal, issue_token
from app.services import audit

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=200)


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"  # noqa: S105 - OAuth token type, not a secret
    user: dict[str, Any]


@router.post("/login", response_model=LoginResponse)
async def login(body: LoginRequest, services: ServicesDep) -> LoginResponse:
    async with services.db.system_session() as session:
        user = await session.scalar(select(User).where(User.email == body.email.lower().strip()))
        # Same error for unknown user and wrong password (no account enumeration).
        if user is None or not user.is_active or not verify_password(body.password, user.password_hash):
            raise AuthenticationError("Invalid email or password")
        bank = await session.get(Bank, user.bank_id)
        principal = Principal(user.id, user.bank_id, user.role, user.email)
    async with services.db.tenant_session(principal.bank_id) as session:
        await audit.record(
            session,
            bank_id=principal.bank_id,
            actor=audit.Actor.user(principal.user_id),
            action="auth.login",
            subject_type="user",
            subject_id=principal.user_id,
        )
    return LoginResponse(
        access_token=issue_token(services.settings, principal),
        user={
            "id": str(user.id),
            "email": user.email,
            "name": user.full_name,
            "role": user.role,
            "bank": bank.name if bank else None,
            "bank_id": str(user.bank_id),
        },
    )


@router.get("/me")
async def me(principal: PrincipalDep, services: ServicesDep) -> dict[str, Any]:
    async with services.db.system_session() as session:
        user = await session.get(User, principal.user_id)
        bank = await session.get(Bank, principal.bank_id)
    return {
        "id": str(principal.user_id),
        "email": principal.email,
        "role": principal.role,
        "name": user.full_name if user else None,
        "bank": bank.name if bank else None,
        "bank_profile": bank.profile if bank else {},
        "llm_available": services.llm.available,
    }
