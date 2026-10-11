"""FastAPI dependencies. Resources live on app.state (created in the lifespan),
never in module globals, so tests can build isolated app instances."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import Settings
from app.container import Services
from app.core.errors import AuthenticationError, AuthorizationError
from app.db import Database
from app.llm import LLMStack
from app.security.rbac import Permission, has_permission
from app.security.tokens import Principal, decode_token

_bearer = HTTPBearer(auto_error=False)


def get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_database(request: Request) -> Database:
    db: Database = request.app.state.services.db
    return db


def get_llm(request: Request) -> LLMStack:
    stack: LLMStack = request.app.state.services.llm_stack
    return stack


def get_services(request: Request) -> Services:
    services: Services = request.app.state.services
    return services


async def get_principal(
    request: Request, credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)]
) -> Principal:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise AuthenticationError("Missing bearer token")
    return decode_token(request.app.state.settings, credentials.credentials)


def require(permission: Permission) -> Callable[[Principal], Awaitable[Principal]]:
    async def check(principal: Annotated[Principal, Depends(get_principal)]) -> Principal:
        if not has_permission(principal.role, permission):
            raise AuthorizationError(f"Role '{principal.role}' lacks permission '{permission.value}'")
        return principal

    return check


SettingsDep = Annotated[Settings, Depends(get_settings)]
DatabaseDep = Annotated[Database, Depends(get_database)]
LLMDep = Annotated[LLMStack, Depends(get_llm)]
ServicesDep = Annotated[Services, Depends(get_services)]
PrincipalDep = Annotated[Principal, Depends(get_principal)]
Reader = Annotated[Principal, Depends(require(Permission.READ))]
Ingestor = Annotated[Principal, Depends(require(Permission.INGEST))]
Analyst = Annotated[Principal, Depends(require(Permission.RUN_ANALYSIS))]
Reviewer = Annotated[Principal, Depends(require(Permission.REVIEW))]
AuditReader = Annotated[Principal, Depends(require(Permission.READ_AUDIT))]
