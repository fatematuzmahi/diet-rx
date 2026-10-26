"""
Shared authentication and role-based authorization helpers.

Supports PATIENT, DOCTOR, and ADMIN roles.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from fastapi import Depends, Header, HTTPException, status
from jose import JWTError, jwt

from backend.routers.auth_router import ALGORITHM, SECRET_KEY


ACCESS_TOKEN_TYPE = "access"

ROLE_PATIENT = "PATIENT"
ROLE_DOCTOR = "DOCTOR"
ROLE_ADMIN = "ADMIN"


@dataclass(frozen=True)
class Principal:
    """Authenticated identity extracted from an access token."""

    subject: str
    role: str

    @property
    def is_patient(self) -> bool:
        return self.role == ROLE_PATIENT

    @property
    def is_doctor(self) -> bool:
        return self.role == ROLE_DOCTOR

    @property
    def is_admin(self) -> bool:
        return self.role == ROLE_ADMIN


def _unauthorized(
    detail: str = "Invalid or expired authentication token.",
) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def decode_access_token(token: str) -> Principal:
    """
    Decode and validate an access token.

    Only tokens with a valid subject, supported role, and access-token
    type are accepted.
    """
    try:
        payload = jwt.decode(
            token,
            SECRET_KEY,
            algorithms=[ALGORITHM],
        )
    except JWTError as exc:
        raise _unauthorized() from exc

    subject = payload.get("sub")
    role = payload.get("role")
    token_type = payload.get("typ")

    if not subject or role not in (
        ROLE_PATIENT,
        ROLE_DOCTOR,
        ROLE_ADMIN,
    ):
        raise _unauthorized(
            "Authentication token is missing required claims."
        )

    if token_type != ACCESS_TOKEN_TYPE:
        raise _unauthorized("A valid access token is required.")

    return Principal(
        subject=str(subject),
        role=role,
    )


def require_authenticated(
    authorization: str | None = Header(default=None),
) -> Principal:
    """Require a valid Bearer access token."""
    if not authorization:
        raise _unauthorized("Authorization header is required.")

    scheme, separator, token = authorization.partition(" ")

    if (
        not separator
        or scheme.lower() != "bearer"
        or not token.strip()
    ):
        raise _unauthorized("A valid Bearer token is required.")

    return decode_access_token(token.strip())


def require_role(*allowed_roles: str):
    """Create a dependency that allows only the specified roles."""

    normalized_roles = {
        role.upper()
        for role in allowed_roles
    }

    def dependency(
        principal: Principal = Depends(require_authenticated),
    ) -> Principal:
        if principal.role not in normalized_roles:
            allowed = ", ".join(sorted(normalized_roles))

            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"This operation requires role(s): {allowed}. "
                    f"Your role is {principal.role}."
                ),
            )

        return principal

    return dependency


def require_self_or_doctor(
    resource_user_id: str | int,
    principal: Principal = Depends(require_authenticated),
) -> Principal:
    """
    Allow patients to access their own resources and doctors to access
    resources under the existing doctor-access policy.

    ADMIN is not automatically treated as DOCTOR.
    """
    if principal.role == ROLE_DOCTOR:
        return principal

    if (
        principal.role == ROLE_PATIENT
        and principal.subject == str(resource_user_id)
    ):
        return principal

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="You do not have permission to access this resource.",
    )


require_patient = require_role(ROLE_PATIENT)
require_doctor = require_role(ROLE_DOCTOR)
require_admin = require_role(ROLE_ADMIN)
require_doctor_or_admin = require_role(
    ROLE_DOCTOR,
    ROLE_ADMIN,
)