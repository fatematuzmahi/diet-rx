"""SCRUM-49: let a patient ACCEPT a generated plan (SRS 3.1.3.8.5).

Adds two columns to DIET_PLAN, only if they are missing:

    ACCEPTED_AT  TIMESTAMP  -- when the patient accepted this plan
    ACCEPTED_BY  NUMBER     -- the patient id that accepted it

Safe to run more than once. It never drops, deletes or changes any data.

Run from the project root:
    python backend/database/apply_meal_plan_scrum49.py
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.database.connection import get_connection  # noqa: E402

DIET_PLAN_COLUMNS = [
    ("ACCEPTED_AT", "TIMESTAMP"),
    ("ACCEPTED_BY", "NUMBER"),
]


def columns_of(cur, name):
    cur.execute("SELECT column_name FROM user_tab_columns WHERE table_name = :t", t=name)
    return {row[0] for row in cur.fetchall()}


def main():
    conn = get_connection()
    if conn is None:
        print("Database connection failed. Nothing was changed.")
        return
    cur = conn.cursor()
    try:
        existing = columns_of(cur, "DIET_PLAN")
        if not existing:
            print("DIET_PLAN table was not found. Nothing was changed.")
            return
        print("--- DIET_PLAN ---")
        for column, definition in DIET_PLAN_COLUMNS:
            if column in existing:
                print(f"  already exists : DIET_PLAN.{column}")
            else:
                cur.execute(f"ALTER TABLE diet_plan ADD ({column} {definition})")
                print(f"  ADDED          : DIET_PLAN.{column}")
        print("\nDone.")
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()