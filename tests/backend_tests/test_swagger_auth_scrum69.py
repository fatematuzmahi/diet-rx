"""SCRUM-69 (round 2) tests: the Swagger "Authorize" button.

Found in the Sprint 5 demo: Swagger UI never sends a plain header parameter
named Authorization, so every protected route answered 401 from /docs even
with a valid token. Protected routes now declare a Bearer security scheme.
The token is still read from the same Authorization header, so the frontend
and all other tests behave exactly as before.
"""

import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key")

import pytest
from fastapi import HTTPException

from backend.core.security import require_authenticated
from backend.main import app

BEARER = {"BearerToken": []}

PROTECTED = [
    ("/api/health-profile/me", "get"),
    ("/api/dish-dna", "post"),
    ("/api/daily-log/trend", "get"),
    ("/api/budget/me", "put"),
    ("/api/sharing/me", "put"),
    ("/api/doctor/reviews", "post"),
    ("/api/admin/users", "get"),
]

PUBLIC = [
    ("/auth/register", "post"),
    ("/auth/login", "post"),
    ("/auth/doctor/login", "post"),
    ("/auth/admin/login", "post"),
]


def _schema():
    app.openapi_schema = None  # build fresh from the current routes
    return app.openapi()


def test_swagger_has_a_bearer_authorize_button():
    scheme = _schema()["components"]["securitySchemes"]["BearerToken"]
    assert scheme["type"] == "http" and scheme["scheme"].lower() == "bearer"


@pytest.mark.parametrize("path,method", PROTECTED)
def test_protected_routes_use_the_bearer_scheme(path, method):
    operation = _schema()["paths"][path][method]
    assert BEARER in operation.get("security", [])


@pytest.mark.parametrize("path,method", PUBLIC)
def test_login_and_register_stay_public(path, method):
    assert "security" not in _schema()["paths"][path][method]


def test_no_plain_authorization_field_is_shown_in_swagger():
    for path, operations in _schema()["paths"].items():
        for method, operation in operations.items():
            names = [p["name"].lower() for p in operation.get("parameters", [])]
            assert "authorization" not in names, f"{method.upper()} {path}"


def test_missing_token_is_still_401_with_the_same_message():
    with pytest.raises(HTTPException) as error:
        asyncio.run(require_authenticated(authorization=None))
    assert error.value.status_code == 401
    assert error.value.detail == "Missing Authorization header."


def test_wrong_scheme_is_still_401_with_the_same_message():
    with pytest.raises(HTTPException) as error:
        asyncio.run(require_authenticated(authorization="Basic abc"))
    assert error.value.status_code == 401
    assert error.value.detail == "Authorization header must be 'Bearer <token>'."
