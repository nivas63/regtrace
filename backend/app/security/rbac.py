"""Roles and permissions.

Permissions are coarse-grained capabilities checked at the API boundary. Segregation
of duties (a proposer cannot approve their own high-impact item) is enforced in the
review service, not here.
"""

from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    ADMIN = "admin"
    ANALYST = "analyst"  # compliance analyst: ingest, run analyses
    REVIEWER = "reviewer"  # compliance officer: approve/reject
    CONTROL_OWNER = "control_owner"
    AUDITOR = "auditor"  # read-only incl. audit trail


class Permission(StrEnum):
    READ = "read"
    INGEST = "ingest"
    RUN_ANALYSIS = "run_analysis"
    REVIEW = "review"
    MANAGE_CONTROLS = "manage_controls"
    READ_AUDIT = "read_audit"
    ADMIN = "admin"


_ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.ADMIN: frozenset(Permission),
    Role.ANALYST: frozenset({Permission.READ, Permission.INGEST, Permission.RUN_ANALYSIS, Permission.MANAGE_CONTROLS}),
    Role.REVIEWER: frozenset({Permission.READ, Permission.REVIEW, Permission.RUN_ANALYSIS, Permission.READ_AUDIT}),
    Role.CONTROL_OWNER: frozenset({Permission.READ, Permission.MANAGE_CONTROLS}),
    Role.AUDITOR: frozenset({Permission.READ, Permission.READ_AUDIT}),
}


def has_permission(role: str, permission: Permission) -> bool:
    try:
        return permission in _ROLE_PERMISSIONS[Role(role)]
    except ValueError:
        return False
