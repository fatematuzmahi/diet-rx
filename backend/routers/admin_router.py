
"""
SCRUM-61: Admin Panel API.

GET /api/admin/users
    Admin-only user listing with search, role filter and pagination.

Security:
- Only authenticated ADMIN users can access this router.
- Passwords, password hashes and tokens are never returned.
"""

from __future__ import annotations

import logging
from typing import Optional

import oracledb
from fastapi import APIRouter, Depends, HTTPException, Query

from backend.core.security import Principal, require_admin
from backend.database.connection import get_connection

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/admin",
    tags=["Admin"],
)


def _connect():
    connection = get_connection()

    if connection is None:
        raise HTTPException(
            status_code=503,
            detail="Database connection failed.",
        )

    return connection


def _oracle_error_code(error: oracledb.Error) -> Optional[int]:
    error_info = error.args[0] if error.args else None
    return getattr(error_info, "code", None)


@router.get("/users")
def list_admin_users(
    role: Optional[str] = Query(default=None, max_length=20),
    q: Optional[str] = Query(default=None, max_length=100),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    principal: Principal = Depends(require_admin),
):
    """
    Return a paginated list of patients and doctors.

    Search matches name or email, case-insensitively.
    Passwords and password hashes are never selected.
    """

    normalized_role = role.strip().upper() if role and role.strip() else None

    if normalized_role not in (None, "PATIENT", "DOCTOR"):
        raise HTTPException(
            status_code=422,
            detail="Role must be PATIENT or DOCTOR.",
        )

    search = q.strip() if q and q.strip() else None

    connection = _connect()
    cursor = connection.cursor()

    try:
        # CREATED_AT exists in PATIENT, but not in DOCTOR.
        # Therefore doctor created_at is returned as NULL.
        union_sql = """
            SELECT
                patient_id AS user_id,
                name,
                email,
                'PATIENT' AS role,
                created_at
            FROM patient

            UNION ALL

            SELECT
                doctor_id AS user_id,
                name,
                email,
                'DOCTOR' AS role,
                CAST(NULL AS TIMESTAMP) AS created_at
            FROM doctor
        """

        filters = []
        binds = {}

        if normalized_role:
            filters.append("role = :role")
            binds["role"] = normalized_role

        if search:
            escaped_search = (
                search.replace("\\", "\\\\")
                .replace("%", "\\%")
                .replace("_", "\\_")
            )

            filters.append(
                "(LOWER(name) LIKE LOWER(:search) ESCAPE '\\' "
                "OR LOWER(email) LIKE LOWER(:search) ESCAPE '\\')"
            )
            binds["search"] = f"%{escaped_search}%"

        filtered_sql = f"SELECT * FROM ({union_sql})"

        if filters:
            filtered_sql += " WHERE " + " AND ".join(filters)

        cursor.execute(
            f"SELECT COUNT(*) FROM ({filtered_sql})",
            **binds,
        )
        total = int(cursor.fetchone()[0])

        page_sql = (
            "SELECT user_id, name, email, role, created_at "
            f"FROM ({filtered_sql}) "
            "ORDER BY role, name, user_id "
            "OFFSET :offset ROWS FETCH NEXT :limit ROWS ONLY"
        )

        page_binds = {
            **binds,
            "offset": offset,
            "limit": limit,
        }

        cursor.execute(page_sql, **page_binds)
        rows = cursor.fetchall() or []

        items = [
            {
                "user_id": int(row[0]),
                "name": row[1],
                "email": row[2],
                "role": row[3],
                "created_at": (
                    row[4].isoformat() if row[4] is not None else None
                ),
            }
            for row in rows
        ]

        return {
            "total": total,
            "limit": limit,
            "offset": offset,
            "items": items,
        }

    except HTTPException:
        raise

    except oracledb.Error as error:
        code = _oracle_error_code(error)

        logger.error(
            "SCRUM-61 admin user listing failed (Oracle code: %s)",
            code,
        )

        if code in (942, 904):
            raise HTTPException(
                status_code=503,
                detail=(
                    "User listing is unavailable because "
                    "the database schema is not ready."
                ),
            )

        raise HTTPException(
            status_code=500,
            detail="Unable to load users right now.",
        )

    finally:
        cursor.close()
        connection.close()
