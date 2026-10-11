from __future__ import annotations

import uuid

import pytest

from app.config import Settings
from app.core.errors import AuthenticationError
from app.security.passwords import hash_password, verify_password
from app.security.rbac import Permission, has_permission
from app.security.tokens import Principal, decode_token, issue_token


def test_password_roundtrip() -> None:
    stored = hash_password("s3cret!")
    assert verify_password("s3cret!", stored)
    assert not verify_password("wrong", stored)
    assert not verify_password("s3cret!", "garbage")


def test_token_roundtrip_and_tamper() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    principal = Principal(uuid.uuid4(), uuid.uuid4(), "analyst", "a@example.com")
    token = issue_token(settings, principal)
    assert decode_token(settings, token) == principal
    with pytest.raises(AuthenticationError):
        decode_token(settings, token[:-2] + "xx")


def test_rbac() -> None:
    assert has_permission("reviewer", Permission.REVIEW)
    assert not has_permission("analyst", Permission.REVIEW)
    assert not has_permission("auditor", Permission.INGEST)
    assert not has_permission("unknown-role", Permission.READ)
