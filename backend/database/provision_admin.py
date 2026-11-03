
"""
SCRUM-61: Provision the first admin account.

Run from the project root:
    python -m backend.database.provision_admin

This script creates an admin account in the existing ADMIN_USER table.
There is no public admin registration.
"""

import getpass
import re
import sys
from pathlib import Path

# Support running from the project root.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import oracledb
from werkzeug.security import generate_password_hash

from backend.database.connection import get_connection


TABLE_EXISTS_QUERY = """
SELECT COUNT(*)
FROM user_tables
WHERE table_name = :table_name
"""

EMAIL_EXISTS_QUERY = """
SELECT COUNT(*)
FROM ADMIN_USER
WHERE LOWER(EMAIL) = LOWER(:email)
"""

INSERT_ADMIN_SQL = """
INSERT INTO ADMIN_USER (EMAIL, PASSWORD_HASH)
VALUES (:email, :password_hash)
"""


def provision_admin() -> None:
    """Create the first admin account after interactive validation."""

    print("=== SCRUM-61: Create Admin Account ===")

    email = input("Admin email: ").strip().lower()

    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        print("Invalid email address.")
        return

    password = getpass.getpass("Admin password (minimum 12 characters): ")

    if len(password) < 12:
        print("Password must contain at least 12 characters.")
        return

    confirm_password = getpass.getpass("Confirm admin password: ")

    if password != confirm_password:
        print("Passwords do not match.")
        return

    connection = get_connection()

    if connection is None:
        print("Database connection failed.")
        return

    try:
        with connection.cursor() as cursor:
            cursor.execute(
                TABLE_EXISTS_QUERY,
                table_name="ADMIN_USER",
            )
            table_exists = cursor.fetchone()[0] > 0

            if not table_exists:
                print(
                    "ADMIN_USER table does not exist. "
                    "Run apply_admin_user.py first."
                )
                return

            cursor.execute(
                EMAIL_EXISTS_QUERY,
                email=email,
            )

            if cursor.fetchone()[0] > 0:
                print("An admin account with this email already exists.")
                return

            password_hash = generate_password_hash(password)

            cursor.execute(
                INSERT_ADMIN_SQL,
                email=email,
                password_hash=password_hash,
            )

            connection.commit()
            print("Admin account created successfully.")

    except oracledb.Error as error:
        connection.rollback()
        print("Database error: unable to create the admin account.")
        print(f"Oracle error code: {getattr(error.args[0], 'code', 'unknown')}")

    except Exception:
        connection.rollback()
        print("Unable to create the admin account.")
        raise

    finally:
        connection.close()
        password = None
        confirm_password = None


if __name__ == "__main__":
    provision_admin()
