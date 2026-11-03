"""SCRUM-62 tests: security hardening.

* The JWT key must come from .env; the old public default is never accepted.
* A token signed with the old public default key is rejected (401).
* Browser security headers are added to every response.

Runs without Oracle and without an HTTP client.
"""

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key")

import pytest
from fastapi import HTTPException
from jose import jwt

from backend.core.security import decode_access_token
from backend.core.security_headers import apply_security_headers
from backend.routers import auth_router, password_router
from backend.routers.auth_router import ALGORITHM, load_secret_key

OLD_PUBLIC_KEY = "dietrx-secret-key"


def _token(key, **claims):
    body = {"sub": "5", "role": "PATIENT", "typ": "access",
            "exp": datetime.now(timezone.utc) + timedelta(minutes=5)}
    body.update(claims)
    return jwt.encode(body, key, algorithm=ALGORITHM)


# --------------------------------------------------------------------------- #
# Secret key
# --------------------------------------------------------------------------- #
def test_missing_secret_key_stops_the_server_with_a_clear_message():
    with pytest.raises(RuntimeError) as error:
        load_secret_key({})
    assert "JWT_SECRET_KEY" in str(error.value) and ".env" in str(error.value)


def test_blank_secret_key_is_treated_as_missing():
    with pytest.raises(RuntimeError):
        load_secret_key({"JWT_SECRET_KEY": "   "})


def test_old_public_default_key_is_refused():
    with pytest.raises(RuntimeError) as error:
        load_secret_key({"JWT_SECRET_KEY": OLD_PUBLIC_KEY})
    assert "old public default" in str(error.value)


def test_secret_key_from_env_is_used():
    assert load_secret_key({"JWT_SECRET_KEY": "  a-long-random-value  "}) == "a-long-random-value"


def test_server_never_runs_with_the_old_public_key():
    assert auth_router.SECRET_KEY != OLD_PUBLIC_KEY
    assert password_router.RESET_SESSION_SECRET == auth_router.SECRET_KEY


def test_source_has_no_hard_coded_fallback_key():
    root = Path(__file__).resolve().parents[2] / "backend" / "routers"
    for name in ("auth_router.py", "password_router.py"):
        text = (root / name).read_text(encoding="utf-8")
        assert 'getenv("JWT_SECRET_KEY", "' not in text


def test_token_forged_with_the_old_public_key_is_401():
    with pytest.raises(HTTPException) as error:
        decode_access_token(_token(OLD_PUBLIC_KEY))
    assert error.value.status_code == 401


def test_token_signed_with_the_real_key_still_works():
    principal = decode_access_token(_token(auth_router.SECRET_KEY))
    assert principal.subject == "5" and principal.role == "PATIENT"


# --------------------------------------------------------------------------- #
# Security headers
# --------------------------------------------------------------------------- #
def test_every_response_gets_the_browser_security_headers():
    headers = apply_security_headers("/", {})
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["X-Frame-Options"] == "DENY"
    assert headers["Referrer-Policy"] == "no-referrer"


def test_api_and_auth_responses_are_never_cached():
    assert apply_security_headers("/api/health-profile/me", {})["Cache-Control"] == "no-store"
    assert apply_security_headers("/auth/login", {})["Cache-Control"] == "no-store"
    assert "Cache-Control" not in apply_security_headers("/", {})


def test_existing_header_values_are_kept():
    headers = apply_security_headers("/api/prices", {"Cache-Control": "max-age=60"})
    assert headers["Cache-Control"] == "max-age=60"
