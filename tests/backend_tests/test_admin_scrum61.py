"""SCRUM-61 tests: admin role, admin login and admin user list.

Runs without Oracle: small fake connections answer the queries.
"""

import asyncio
import inspect
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key")

import pytest
from fastapi import HTTPException
from werkzeug.security import generate_password_hash

from backend.core.security import (
    ROLE_ADMIN,
    Principal,
    decode_access_token,
    require_admin,
    require_doctor_or_admin,
    require_self_or_doctor,
)
from backend.routers import admin_router, auth_router, dish_dna_router, price_router
from backend.routers.auth_router import LoginRequest, create_access_token

ADMIN = Principal(subject="1", role="ADMIN")
DOCTOR = Principal(subject="1", role="DOCTOR")
PATIENT = Principal(subject="1", role="PATIENT")


def run(value):
    return asyncio.run(value) if inspect.iscoroutine(value) else value


def status_of(call):
    with pytest.raises(HTTPException) as error:
        run(call())
    return error.value.status_code


# --------------------------------------------------------------------------- #
# Role rules
# --------------------------------------------------------------------------- #
def test_admin_token_is_accepted():
    token = create_access_token({"sub": "1", "role": "ADMIN", "typ": "access"})
    assert decode_access_token(token).role == ROLE_ADMIN


def test_require_admin_lets_only_admin_in():
    assert run(require_admin(principal=ADMIN)) is ADMIN
    assert status_of(lambda: require_admin(principal=DOCTOR)) == 403
    assert status_of(lambda: require_admin(principal=PATIENT)) == 403


def test_data_maintainer_role_is_doctor_or_admin_never_patient():
    assert run(require_doctor_or_admin(principal=ADMIN)) is ADMIN
    assert run(require_doctor_or_admin(principal=DOCTOR)) is DOCTOR
    assert status_of(lambda: require_doctor_or_admin(principal=PATIENT)) == 403


def test_admin_is_not_treated_as_a_doctor_for_patient_records():
    assert status_of(lambda: require_self_or_doctor(patient_id=1, principal=ADMIN)) == 403


def test_admin_can_maintain_prices_and_dish_dna_but_not_delete_dishes():
    for fn in (price_router.add_price, price_router.update_price,
               dish_dna_router.create_dish, dish_dna_router.update_dish):
        assert inspect.signature(fn).parameters["principal"].default.dependency is require_doctor_or_admin
    assert inspect.signature(dish_dna_router.delete_dish).parameters["principal"].default.dependency \
        is not require_doctor_or_admin


def test_user_list_is_admin_only():
    dep = inspect.signature(admin_router.list_admin_users).parameters["principal"].default.dependency
    assert dep is require_admin


# --------------------------------------------------------------------------- #
# Admin login
# --------------------------------------------------------------------------- #
class LoginCursor:
    def __init__(self, row):
        self.row, self.sql = row, ""

    def execute(self, sql, **binds):
        self.sql, self.binds = sql, binds

    def fetchone(self):
        return self.row

    def close(self):
        pass


class LoginConnection:
    def __init__(self, row):
        self.cur = LoginCursor(row)

    def cursor(self):
        return self.cur

    def close(self):
        pass


def admin_login(monkeypatch, row, email="Admin@DietRx.test", password="Correct#Password1"):
    connection = LoginConnection(row)
    monkeypatch.setattr(auth_router, "get_connection", lambda: connection)
    return auth_router.login_admin(LoginRequest(email=email, password=password)), connection


def test_admin_login_gives_an_admin_token(monkeypatch):
    row = (1, "admin@dietrx.test", generate_password_hash("Correct#Password1"))
    result, connection = admin_login(monkeypatch, row)
    assert decode_access_token(result["access_token"]).role == "ADMIN"
    assert "password" not in str(result["user"]).lower()
    assert connection.cur.binds["email"] == "admin@dietrx.test"
    assert "ADMIN_USER" in connection.cur.sql.upper()


def test_admin_login_wrong_password_is_401(monkeypatch):
    row = (1, "admin@dietrx.test", generate_password_hash("Correct#Password1"))
    with pytest.raises(HTTPException) as error:
        admin_login(monkeypatch, row, password="wrong")
    assert error.value.status_code == 401


def test_admin_login_unknown_email_gives_the_same_401(monkeypatch):
    with pytest.raises(HTTPException) as error:
        admin_login(monkeypatch, None)
    assert error.value.status_code == 401 and error.value.detail == "Invalid email or password."


def test_admin_login_without_database_is_503(monkeypatch):
    monkeypatch.setattr(auth_router, "get_connection", lambda: None)
    with pytest.raises(HTTPException) as error:
        auth_router.login_admin(LoginRequest(email="a@b.co", password="x"))
    assert error.value.status_code == 503


def test_there_is_no_public_admin_sign_up():
    paths = {getattr(r, "path", "") for r in getattr(auth_router.router, "routes", [])}
    assert not any("admin/register" in p for p in paths)
    assert not hasattr(auth_router, "register_admin")


# --------------------------------------------------------------------------- #
# Admin user list
# --------------------------------------------------------------------------- #
class ListCursor:
    def __init__(self):
        self.calls = []

    def execute(self, sql, **binds):
        self.calls.append((" ".join(sql.split()), binds))

    def fetchone(self):
        return (1,)

    def fetchall(self):
        return [(5, "Taima Tarima", "taima@example.com", "PATIENT", None)]

    def close(self):
        pass


class ListConnection:
    def __init__(self):
        self.cur = ListCursor()

    def cursor(self):
        return self.cur

    def close(self):
        pass


def test_user_list_never_reads_or_returns_passwords(monkeypatch):
    connection = ListConnection()
    monkeypatch.setattr(admin_router, "get_connection", lambda: connection)
    result = admin_router.list_admin_users(role=None, q=None, limit=50, offset=0, principal=ADMIN)
    assert result["items"][0] == {"user_id": 5, "name": "Taima Tarima", "email": "taima@example.com",
                                  "role": "PATIENT", "created_at": None}
    for sql, _binds in connection.cur.calls:
        assert "password" not in sql.lower()


def test_user_search_is_a_bind_value_not_sql(monkeypatch):
    connection = ListConnection()
    monkeypatch.setattr(admin_router, "get_connection", lambda: connection)
    admin_router.list_admin_users(role="patient", q="x' OR 1=1 --", limit=10, offset=0, principal=ADMIN)
    sql, binds = connection.cur.calls[-1]
    assert "1=1" not in sql and binds["search"] == "%x' OR 1=1 --%" and binds["role"] == "PATIENT"


def test_unknown_role_filter_is_422():
    with pytest.raises(HTTPException) as error:
        admin_router.list_admin_users(role="ADMIN", q=None, limit=10, offset=0, principal=ADMIN)
    assert error.value.status_code == 422
