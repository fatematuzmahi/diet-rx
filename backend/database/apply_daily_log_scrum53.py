"""SCRUM-53: create the DAILY_LOG table if it does not exist yet.

Safe to run more than once:
  * if DAILY_LOG already exists, it only prints the current columns;
  * it never drops, deletes, truncates or recreates anything.

Run from the project root:
    python backend/database/apply_daily_log_scrum53.py
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.database.connection import get_connection  # noqa: E402

CREATE_TABLE = """
CREATE TABLE daily_log (
    log_id      NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    patient_id  NUMBER NOT NULL,
    plan_id     NUMBER,
    log_type    VARCHAR2(20) NOT NULL
                CHECK (log_type IN ('MEAL', 'WATER', 'ACTIVITY', 'MEDICATION', 'GOAL')),
    log_date    DATE NOT NULL,
    log_time    VARCHAR2(5),
    slot        VARCHAR2(20),
    title       VARCHAR2(150),
    dish_id     NUMBER,
    quantity    NUMBER(8, 2),
    unit        VARCHAR2(20),
    status      VARCHAR2(20),
    notes       VARCHAR2(500),
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_daily_log_patient FOREIGN KEY (patient_id)
        REFERENCES patient(patient_id) ON DELETE CASCADE,
    CONSTRAINT fk_daily_log_plan FOREIGN KEY (plan_id)
        REFERENCES diet_plan(plan_id) ON DELETE SET NULL,
    CONSTRAINT fk_daily_log_dish FOREIGN KEY (dish_id)
        REFERENCES dish_dna(dish_id) ON DELETE SET NULL
)
"""

CREATE_INDEX = "CREATE INDEX idx_daily_log_patient_date ON daily_log(patient_id, log_date)"


def main():
    conn = get_connection()
    if conn is None:
        print("Database connection failed. Nothing was changed.")
        return
    cur = conn.cursor()
    try:
        cur.execute("SELECT COUNT(*) FROM user_tables WHERE table_name = 'DAILY_LOG'")
        exists = cur.fetchone()[0] > 0

        if exists:
            print("DAILY_LOG already exists -- nothing was changed.")
        else:
            cur.execute(CREATE_TABLE)
            print("CREATED table : DAILY_LOG")
            cur.execute(CREATE_INDEX)
            print("CREATED index : IDX_DAILY_LOG_PATIENT_DATE")

        cur.execute(
            "SELECT column_name FROM user_tab_columns "
            "WHERE table_name = 'DAILY_LOG' ORDER BY column_id"
        )
        print("\nDAILY_LOG columns:")
        print("  " + ", ".join(row[0] for row in cur.fetchall()))
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()