"""SCRUM-44: Role-based access control primitives for the FastAPI backend.

Design notes
------------
* Roles are IMPLICIT, not stored in a column. A principal is a PATIENT, a
  DOCTOR or (SCRUM-61) an ADMIN purely by virtue of which table it
  authenticated against (PATIENT, DOCTOR, ADMIN_USER). There is no role column.
  ADMIN is never treated as a DOCTOR: it can only reach routes that name it.

* ``PATIENT_ID`` and ``DOCTOR_ID`` are independent identity sequences that both
  start at 1, so a bare numeric id is ambiguous across the two principals.
  Every authorization decision therefore resolves on the ``(role, subject)``
  pair and NEVER on the numeric id alone.

* Access tokens carry ``typ == "access"`` so the existing
  ``purpose == "password_reset"`` cookie JWT cannot be replayed as an access
  token, and vice versa.

Status code contract
--------------------
* 401 -- missing / malformed / bad-signature / expired / wrong-purpose token,
         or an unknown principal.
* 403 -- authenticated principal that lacks the required role, or a PATIENT
         principal attempting to read another patient's data.

This module never touches the database for authentication: token verification
is purely cryptographic, so no query is needed to establish identity.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from backend.routers.auth_router import ALGORITHM, SECRET_KEY

ACCESS_TOKEN_TYPE = "access"

ROLE_PATIENT = "PATIENT"
ROLE_DOCTOR = "DOCTOR"
ROLE_ADMIN = "ADMIN"  # SCRUM-61

CREDENTIALS_EXCEPTION_HEADERS = {"WWW-Authenticate": "Bearer"}

# Swagger "Authorize" button. Swagger UI never sends a plain header parameter
# named Authorization, so protected routes declare this Bearer scheme instead.
# auto_error=False: the checks below still produce the same 401 messages.
bearer_scheme = HTTPBearer(
    auto_error=False,
    scheme_name="BearerToken",
    description="Paste the access_token from /auth/login (without the word Bearer).",
)


@dataclass(frozen=True)
class Principal:
    """The authenticated subject carried by a verified access token.

    ``subject`` is the raw id of the authenticating row and is only meaningful
    together with ``role``.
    """

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


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers=CREDENTIALS_EXCEPTION_HEADERS,
    )


def _forbidden(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=detail,
    )


def decode_access_token(token: str) -> Principal:
    """Verify an access token and return its principal.

    Raises ``HTTPException`` 401 for any token that cannot be trusted.
    """

    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        # Covers bad signature, malformed token and expiry.
        raise _unauthorized("Invalid or expired authentication token.")

    # A password-reset token must never be usable as an access token.
    if payload.get("typ") != ACCESS_TOKEN_TYPE:
        raise _unauthorized("Invalid token type for this operation.")

    subject = payload.get("sub")
    role = payload.get("role")
    if not subject or role not in (ROLE_PATIENT, ROLE_DOCTOR, ROLE_ADMIN):
        raise _unauthorized("Authentication token is missing required claims.")

    return Principal(subject=str(subject), role=role)


def _extract_bearer_token(authorization: str | None) -> str:
    if not authorization:
        raise _unauthorized("Missing Authorization header.")

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise _unauthorized("Authorization header must be 'Bearer <token>'.")

    return token.strip()


async def require_authenticated(
    authorization: str | None = Header(default=None, include_in_schema=False),
    _swagger_bearer: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> Principal:
    """FastAPI dependency: any valid access token. 401 otherwise.

    The token is always read from the raw Authorization header, so the
    behaviour is the same for the frontend, tests and Swagger. ``_swagger_bearer``
    only adds the Bearer scheme to the API docs (the Swagger Authorize button).
    """

    return decode_access_token(_extract_bearer_token(authorization))


def require_role(*allowed_roles: str):
    """Build a dependency that admits only the given roles.

    Returns 401 when unauthenticated and 403 when authenticated as the wrong
    role. ``allowed_roles`` is explicit so the permission model is readable at
    the call site rather than buried inside endpoint logic.
    """

    allowed: tuple[str, ...] = tuple(allowed_roles)

    async def dependency(
        principal: Principal = Depends(require_authenticated),
    ) -> Principal:
        if principal.role not in allowed:
            raise _forbidden(
                "This operation requires role "
                f"{{{', '.join(sorted(allowed))}}}; your role is "
                f"{principal.role}."
            )
        return principal

    return dependency


require_patient = require_role(ROLE_PATIENT)
require_doctor = require_role(ROLE_DOCTOR)
require_admin = require_role(ROLE_ADMIN)  # SCRUM-61
require_doctor_or_admin = require_role(ROLE_DOCTOR, ROLE_ADMIN)  # SCRUM-61: data maintainers


async def require_self_or_doctor(
    patient_id: int,
    principal: Principal = Depends(require_authenticated),
) -> Principal:
    """Authorise the patient that owns the data, or any doctor.

    Declared as a dependency that itself takes the ``patient_id`` path
    parameter, so FastAPI injects the resolved path value here. This MUST NOT
    be a factory taking ``patient_id`` — a default-argument expression is
    evaluated at function-definition time, when the path value does not exist.

    A PATIENT principal may only reach its own records. Because ``PATIENT_ID``
    and ``DOCTOR_ID`` share the same numeric space, the owner comparison is
    guarded by the role check so a DOCTOR principal with an equal numeric id can
    never satisfy a patient ownership test.

    Returns 403 for both wrong-role and non-owner cases.
    """

    if principal.is_doctor:
        return principal
    if principal.is_patient and str(patient_id) == principal.subject:
        return principal
    raise _forbidden("You are not permitted to access this patient's data.")


def describe_permission(roles: Iterable[str]) -> str:
    """Human-readable permission label, used in API docs and UI hints."""

    return " or ".join(sorted(roles))
