"""SCRUM-39 / SCRUM-40 / SCRUM-42 tests: registration, verification codes, login,
password reset (SRS 3.1.3.1, 3.1.3.2).

Runs WITHOUT a live Oracle connection: the login query is answered by a fake
connection, and the input checks that run before the database are called
directly. No real password, hash or token is printed.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key")

import pytest
from fastapi import HTTPException
from werkzeug.security import check_password_hash, generate_password_hash

from backend.routers import auth_router
from backend.routers import password_router
from backend.core.security import decode_access_token

ACCOUNTS = {
    "verified@example.com": (5, "Verified Patient", "verified@example.com", generate_password_hash("Correct#123"), 1),
    "new@example.com": (6, "New Patient", "new@example.com", generate_password_hash("Correct#123"), 0),
}


class FakeCursor:
    def __init__(self):
        self.row = None

    def execute(self, sql, **binds):
        self.row = ACCOUNTS.get(binds.get("email"))

    def fetchone(self):
        return self.row

    def close(self):
        pass


class FakeConnection:
    def cursor(self):
        return FakeCursor()

    def close(self):
        pass


@pytest.fixture
def fake_db(monkeypatch):
    monkeypatch.setattr(auth_router, "get_connection", lambda: FakeConnection())


def login(email, password):
    return auth_router.login_user(auth_router.LoginRequest(email=email, password=password))


def register(**overrides):
    data = dict(name="Test", email="t@example.com", age=25, gender="Female", height=160.0,
                weight=55.0, password="Secret#123", confirm_password="Secret#123")
    data.update(overrides)
    return auth_router.register_user(auth_router.RegisterRequest(**data))


# --------------------------------------------------------------------------- #
# Registration (SCRUM-39) and verification codes (SCRUM-40)
# --------------------------------------------------------------------------- #
def test_register_rejects_a_blank_required_field():
    with pytest.raises(HTTPException) as error:
        register(name="   ")
    assert error.value.status_code == 400


def test_register_rejects_mismatched_passwords():
    with pytest.raises(HTTPException) as error:
        register(confirm_password="Different#1")
    assert error.value.status_code == 400
    assert "match" in error.value.detail


def test_verification_code_is_six_digits():
    for _ in range(50):
        code = auth_router.generate_verification_code()
        assert code.isdigit() and len(code) == 6


def test_verification_code_is_stored_as_a_hash_not_plain_text():
    code = auth_router.generate_verification_code()
    stored = auth_router.hash_verification_code(code)
    assert stored != code
    assert check_password_hash(stored, code)


# --------------------------------------------------------------------------- #
# Login (SCRUM-42)
# --------------------------------------------------------------------------- #
def test_login_requires_email_and_password():
    with pytest.raises(HTTPException) as error:
        login("", "")
    assert error.value.status_code == 400


def test_login_unknown_email_is_401(fake_db):
    with pytest.raises(HTTPException) as error:
        login("nobody@example.com", "Correct#123")
    assert error.value.status_code == 401


def test_login_wrong_password_is_401(fake_db):
    with pytest.raises(HTTPException) as error:
        login("verified@example.com", "Wrong#123")
    assert error.value.status_code == 401


def test_login_unverified_account_is_403(fake_db):
    with pytest.raises(HTTPException) as error:
        login("new@example.com", "Correct#123")
    assert error.value.status_code == 403


def test_login_email_is_case_insensitive(fake_db):
    result = login("VERIFIED@example.com", "Correct#123")
    assert result["user"]["patient_id"] == 5


def test_login_success_returns_a_patient_access_token(fake_db):
    result = login("verified@example.com", "Correct#123")
    principal = decode_access_token(result["access_token"])
    assert principal.role == "PATIENT"
    assert principal.subject == "5"
    assert result["user"]["role"] == "PATIENT"
    assert "password" not in result["user"]


def test_login_without_database_is_an_error_not_a_crash(monkeypatch):
    monkeypatch.setattr(auth_router, "get_connection", lambda: None)
    with pytest.raises(HTTPException) as error:
        login("verified@example.com", "Correct#123")
    assert error.value.status_code == 500


# --------------------------------------------------------------------------- #
# Password reset
# --------------------------------------------------------------------------- #
def test_reset_password_rejects_mismatched_passwords():
    with pytest.raises(HTTPException) as error:
        password_router.reset_password(
            password_router.ResetPasswordRequest(new_password="A#123456", confirm_password="B#123456"),
            reset_session=None,
        )
    assert error.value.status_code == 400


def test_reset_password_without_a_reset_session_is_401():
    with pytest.raises(HTTPException) as error:
        password_router.reset_password(
            password_router.ResetPasswordRequest(new_password="A#123456", confirm_password="A#123456"),
            reset_session=None,
        )
    assert error.value.status_code == 401


def test_reset_password_rejects_an_access_token_as_reset_session(fake_db):
    access = login("verified@example.com", "Correct#123")["access_token"]
    with pytest.raises(HTTPException) as error:
        password_router.reset_password(
            password_router.ResetPasswordRequest(new_password="A#123456", confirm_password="A#123456"),
            reset_session=access,
        )
    assert error.value.status_code == 401
