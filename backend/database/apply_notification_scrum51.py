"""SCRUM-51: set up the tables for Smart Notifications & Alerts (SRS 3.1.3.4).

Safe to run more than once. It never drops, deletes or recreates anything:

  * NOTIFICATION (reminder schedules) -- already in schema.sql with only four
    columns. Missing SCRUM-51 columns are ADDED one by one. If the table does
    not exist at all, it is created.
  * NOTIFICATION_EVENT (each time a reminder or safety alert was shown, and
    whether the user acknowledged it) -- created only if it does not exist.

Run from the project root:
    python backend/database/apply_notification_scrum51.py
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.database.connection import get_connection  # noqa: E402

CREATE_NOTIFICATION = """
CREATE TABLE notification (
    notification_id NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    patient_id      NUMBER NOT NULL,
    reminder_type   VARCHAR2(50),
    schedule_time   TIMESTAMP,
    alert_status    NUMBER(1) DEFAULT 0 CHECK (alert_status IN (0, 1)),
    CONSTRAINT fk_notification_patient FOREIGN KEY (patient_id)
        REFERENCES patient(patient_id) ON DELETE CASCADE
)
"""

# (column, definition) -- added to NOTIFICATION only when missing
NOTIFICATION_COLUMNS = [
    ("TITLE", "VARCHAR2(150)"),
    ("MESSAGE", "VARCHAR2(300)"),
    ("REMIND_TIME", "VARCHAR2(5)"),
    ("REPEAT_DAYS", "VARCHAR2(40)"),
    ("ENABLED", "NUMBER(1) DEFAULT 1 CHECK (enabled IN (0, 1))"),
    ("CREATED_AT", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP"),
    ("UPDATED_AT", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP"),
]

CREATE_EVENT = """
CREATE TABLE notification_event (
    event_id        NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    patient_id      NUMBER NOT NULL,
    notification_id NUMBER,
    event_key       VARCHAR2(120) NOT NULL,
    category        VARCHAR2(20) NOT NULL CHECK (category IN ('REMINDER', 'SAFETY')),
    reminder_type   VARCHAR2(20),
    severity        VARCHAR2(10) DEFAULT 'INFO' CHECK (severity IN ('INFO', 'CAUTION', 'HIGH')),
    title           VARCHAR2(150),
    message         VARCHAR2(500),
    event_date      DATE NOT NULL,
    delivered_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    acknowledged_at TIMESTAMP,
    ack_action      VARCHAR2(20),
    CONSTRAINT uq_notification_event UNIQUE (patient_id, event_key),
    CONSTRAINT fk_notif_event_patient FOREIGN KEY (patient_id)
        REFERENCES patient(patient_id) ON DELETE CASCADE,
    CONSTRAINT fk_notif_event_notification FOREIGN KEY (notification_id)
        REFERENCES notification(notification_id) ON DELETE SET NULL
)
"""

CREATE_INDEXES = [
    ("IDX_NOTIFICATION_PATIENT", "CREATE INDEX idx_notification_patient ON notification(patient_id)"),
    ("IDX_NOTIF_EVENT_PATIENT_DATE",
     "CREATE INDEX idx_notif_event_patient_date ON notification_event(patient_id, event_date)"),
]


def table_exists(cur, name):
    cur.execute("SELECT COUNT(*) FROM user_tables WHERE table_name = :t", t=name)
    return cur.fetchone()[0] > 0


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
        print("--- NOTIFICATION ---")
        if not table_exists(cur, "NOTIFICATION"):
            cur.execute(CREATE_NOTIFICATION)
            print("  CREATED table  : NOTIFICATION")
        for column, definition in NOTIFICATION_COLUMNS:
            if column in columns_of(cur, "NOTIFICATION"):
                print(f"  already exists : NOTIFICATION.{column}")
            else:
                cur.execute(f"ALTER TABLE notification ADD ({column} {definition})")
                print(f"  ADDED          : NOTIFICATION.{column}")

        print("\n--- NOTIFICATION_EVENT ---")
        if table_exists(cur, "NOTIFICATION_EVENT"):
            print("  already exists : NOTIFICATION_EVENT")
        else:
            cur.execute(CREATE_EVENT)
            print("  CREATED table  : NOTIFICATION_EVENT")

        print("\n--- Indexes ---")
        cur.execute("SELECT index_name FROM user_indexes")
        existing = {row[0] for row in cur.fetchall()}
        for name, ddl in CREATE_INDEXES:
            if name in existing:
                print(f"  already exists : {name}")
                continue
            try:
                cur.execute(ddl)
                print(f"  CREATED        : {name}")
            except Exception as exc:  # e.g. same columns already indexed
                print(f"  skipped        : {name} ({exc})")

        print("\nNOTIFICATION columns now:")
        cur.execute(
            "SELECT column_name FROM user_tab_columns "
            "WHERE table_name = 'NOTIFICATION' ORDER BY column_id"
        )
        print("  " + ", ".join(row[0] for row in cur.fetchall()))
        print("\nDone.")
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()