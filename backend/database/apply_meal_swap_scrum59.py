"""SCRUM-59: saved cheaper swaps (SRS 3.3.3.2, 3.3.3.5).

Creates the MEAL_SWAP table if it does not exist. One row is one swap the
patient accepted ("buy dal instead of fish"). Undo sets STATUS = 'UNDONE';
rows are never deleted, so the history stays.

Safe to run more than once. It never drops, deletes or truncates anything.

Run from the project root:
    python backend/database/apply_meal_swap_scrum59.py
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.database.connection import get_connection  # noqa: E402

CREATE_MEAL_SWAP = """
CREATE TABLE meal_swap (
    swap_id         NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    patient_id      NUMBER        NOT NULL,
    from_item       VARCHAR2(40)  NOT NULL,
    to_item         VARCHAR2(40)  NOT NULL,
    servings_ratio  NUMBER(4,2)   DEFAULT 1 NOT NULL CHECK (servings_ratio > 0),
    reason          VARCHAR2(400),
    status          VARCHAR2(10)  DEFAULT 'ACTIVE' NOT NULL CHECK (status IN ('ACTIVE', 'UNDONE')),
    created_at      TIMESTAMP     DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP
)
"""

CREATE_INDEX = "CREATE INDEX meal_swap_patient_ix ON meal_swap (patient_id, status)"


def columns_of(cur, name):
    cur.execute("SELECT column_name FROM user_tab_columns WHERE table_name = :t", t=name)
    return {row[0] for row in cur.fetchall()}


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
        print("--- MEAL_SWAP ---")
        if columns_of(cur, "MEAL_SWAP"):
            print("  already exists : MEAL_SWAP")
        else:
            cur.execute(CREATE_MEAL_SWAP)
            print("  CREATED        : MEAL_SWAP")
        if index_exists(cur, "MEAL_SWAP_PATIENT_IX"):
            print("  already exists : MEAL_SWAP_PATIENT_IX")
        else:
            cur.execute(CREATE_INDEX)
            print("  CREATED        : MEAL_SWAP_PATIENT_IX")
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
