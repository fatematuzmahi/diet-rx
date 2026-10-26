"""SCRUM-56: progress sharing + professional review (SRS 3.2.3.5).

Creates two tables if they do not exist:

    SHARE_PERMISSION  what a patient lets one doctor see
                      (Daily Log, plan, progress), one row per patient+doctor.
                      Turning sharing off sets the flags to 0 and STATUS to
                      'REVOKED'; rows are never deleted, so the history stays.

    PLAN_REVIEW       who reviewed which plan, the decision, the note and when
                      (SRS 3.2.3.5.4). Insert-only: a new review is a new row.

Safe to run more than once. It never drops, deletes or truncates anything.

Run from the project root:
    python backend/database/apply_sharing_scrum56.py
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.database.connection import get_connection  # noqa: E402

CREATE_SHARE_PERMISSION = """
CREATE TABLE share_permission (
    share_id         NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    patient_id       NUMBER       NOT NULL,
    doctor_id        NUMBER       NOT NULL,
    share_daily_log  NUMBER(1)    DEFAULT 0 NOT NULL CHECK (share_daily_log IN (0, 1)),
    share_plan       NUMBER(1)    DEFAULT 0 NOT NULL CHECK (share_plan IN (0, 1)),
    share_progress   NUMBER(1)    DEFAULT 0 NOT NULL CHECK (share_progress IN (0, 1)),
    status           VARCHAR2(10) DEFAULT 'ACTIVE' NOT NULL CHECK (status IN ('ACTIVE', 'REVOKED')),
    created_at       TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,
    updated_at       TIMESTAMP,
    CONSTRAINT share_permission_uq UNIQUE (patient_id, doctor_id)
)
"""

CREATE_PLAN_REVIEW = """
CREATE TABLE plan_review (
    review_id    NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    plan_id      NUMBER         NOT NULL,
    patient_id   NUMBER         NOT NULL,
    doctor_id    NUMBER         NOT NULL,
    decision     VARCHAR2(20)   NOT NULL CHECK (decision IN ('APPROVED', 'CHANGES_NEEDED')),
    note         VARCHAR2(1000),
    reviewed_at  TIMESTAMP      DEFAULT CURRENT_TIMESTAMP
)
"""

INDEXES = {
    "SHARE_PERMISSION_DOCTOR_IX": "CREATE INDEX share_permission_doctor_ix ON share_permission (doctor_id, status)",
    "PLAN_REVIEW_PLAN_IX": "CREATE INDEX plan_review_plan_ix ON plan_review (plan_id, reviewed_at)",
    "PLAN_REVIEW_PATIENT_IX": "CREATE INDEX plan_review_patient_ix ON plan_review (patient_id)",
}


def table_exists(cur, name):
    cur.execute("SELECT table_name FROM user_tables WHERE table_name = :t", t=name)
    return cur.fetchone() is not None


def index_exists(cur, name):
    cur.execute("SELECT index_name FROM user_indexes WHERE index_name = :n", n=name)
    return cur.fetchone() is not None


def main():
    conn = get_connection()
    if conn is None:
        print("Database connection failed. Nothing was changed.")
        return
    cur = conn.cursor()
    try:
        for name, ddl in (("SHARE_PERMISSION", CREATE_SHARE_PERMISSION), ("PLAN_REVIEW", CREATE_PLAN_REVIEW)):
            print(f"--- {name} ---")
            if table_exists(cur, name):
                print(f"  already exists : {name}")
            else:
                cur.execute(ddl)
                print(f"  CREATED        : {name}")
        print("--- indexes ---")
        for name, ddl in INDEXES.items():
            if index_exists(cur, name):
                print(f"  already exists : {name}")
            else:
                cur.execute(ddl)
                print(f"  CREATED        : {name}")
        conn.commit()
        print("\nDone.")
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
