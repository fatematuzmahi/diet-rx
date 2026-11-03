"""SCRUM-63: data integrity checks and backup helpers (SRS 3.11.2 Integrity).

Everything here is READ-ONLY: only SELECT statements. Nothing is fixed,
changed or deleted automatically; problems are reported so the team lead can
decide what to do.

A table that does not exist yet (ORA-00942 / ORA-00904) is reported as
"skipped", never as a failure, so the checks work on any database version.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

import oracledb

MISSING_CODES = (942, 904)

# name, what it means, SQL that COUNTS the problem rows
CHECKS = [
    ("orphan_health_profile", "Health profiles whose patient no longer exists",
     "SELECT COUNT(*) FROM health_profile h WHERE NOT EXISTS (SELECT 1 FROM patient p WHERE p.patient_id = h.patient_id)"),
    ("orphan_prescription", "Prescriptions whose patient no longer exists",
     "SELECT COUNT(*) FROM prescription r WHERE NOT EXISTS (SELECT 1 FROM patient p WHERE p.patient_id = r.patient_id)"),
    ("orphan_diet_plan", "Diet plans whose patient no longer exists",
     "SELECT COUNT(*) FROM diet_plan d WHERE NOT EXISTS (SELECT 1 FROM patient p WHERE p.patient_id = d.patient_id)"),
    ("orphan_daily_log", "Daily Log entries whose patient no longer exists",
     "SELECT COUNT(*) FROM daily_log l WHERE NOT EXISTS (SELECT 1 FROM patient p WHERE p.patient_id = l.patient_id)"),
    ("two_current_plans", "Patients with more than one CURRENT diet plan (there must be only one)",
     "SELECT COUNT(*) FROM (SELECT patient_id FROM diet_plan WHERE status = 'CURRENT' "
     "GROUP BY patient_id HAVING COUNT(*) > 1)"),
    ("duplicate_patient_email", "E-mail addresses used by more than one patient account",
     "SELECT COUNT(*) FROM (SELECT LOWER(email) FROM patient GROUP BY LOWER(email) HAVING COUNT(*) > 1)"),
    ("duplicate_doctor_email", "E-mail addresses used by more than one doctor account",
     "SELECT COUNT(*) FROM (SELECT LOWER(email) FROM doctor WHERE email IS NOT NULL "
     "GROUP BY LOWER(email) HAVING COUNT(*) > 1)"),
    ("price_without_source", "Food prices without a positive price, a source or a date",
     "SELECT COUNT(*) FROM food_price WHERE price_bdt IS NULL OR price_bdt <= 0 "
     "OR source IS NULL OR price_date IS NULL"),
    ("price_in_future", "Food prices dated in the future",
     "SELECT COUNT(*) FROM food_price WHERE price_date > SYSDATE"),
    ("negative_budget", "Budgets below zero",
     "SELECT COUNT(*) FROM budget WHERE monthly_budget < 0"),
    ("daily_log_in_future", "Daily Log entries dated in the future",
     "SELECT COUNT(*) FROM daily_log WHERE log_date > TRUNC(SYSDATE) + 1"),
    ("orphan_meal_swap", "Active Smart Swaps whose patient no longer exists",
     "SELECT COUNT(*) FROM meal_swap s WHERE s.status = 'ACTIVE' "
     "AND NOT EXISTS (SELECT 1 FROM patient p WHERE p.patient_id = s.patient_id)"),
    ("active_share_with_nothing", "Active sharing rows where nothing is switched on",
     "SELECT COUNT(*) FROM share_permission WHERE status = 'ACTIVE' "
     "AND share_daily_log = 0 AND share_plan = 0 AND share_progress = 0"),
    ("duplicate_admin_email", "E-mail addresses used by more than one admin account",
     "SELECT COUNT(*) FROM (SELECT LOWER(email) FROM admin_user GROUP BY LOWER(email) HAVING COUNT(*) > 1)"),
    ("review_without_plan", "Professional reviews that point to a plan that does not exist",
     "SELECT COUNT(*) FROM plan_review r WHERE NOT EXISTS (SELECT 1 FROM diet_plan d WHERE d.plan_id = r.plan_id)"),
]

#: Tables copied by the backup script, in a safe restore order (parents first).
BACKUP_TABLES = [
    "patient", "doctor", "health_profile", "prescription", "dish_dna",
    "diet_plan", "activity_plan", "daily_log", "notification", "notification_event",
    "budget", "food_price", "grocery_list", "meal_swap", "share_permission", "plan_review",
]


def oracle_code(error: oracledb.Error) -> Optional[int]:
    return getattr(error.args[0] if error.args else None, "code", None)


def run_checks(cursor) -> List[Dict[str, Any]]:
    """Run every check. Each result: name, description, status (ok|problem|skipped), count."""
    results = []
    for name, description, sql in CHECKS:
        try:
            cursor.execute(sql)
            row = cursor.fetchone()
            count = int(row[0]) if row and row[0] is not None else 0
            results.append({"name": name, "description": description,
                            "status": "ok" if count == 0 else "problem", "count": count})
        except oracledb.Error as error:
            if oracle_code(error) not in MISSING_CODES:
                raise
            results.append({"name": name, "description": description, "status": "skipped", "count": None})
    return results


def summarize(results: List[Dict[str, Any]]) -> Dict[str, int]:
    return {status: sum(1 for r in results if r["status"] == status) for status in ("ok", "problem", "skipped")}


# --------------------------------------------------------------------------- #
# Backup
# --------------------------------------------------------------------------- #
def to_jsonable(value: Any) -> Any:
    """Turn one Oracle value into something json.dumps can write."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    read = getattr(value, "read", None)  # CLOB / BLOB
    if callable(read):
        data = read()
        return data.decode("utf-8", "replace") if isinstance(data, bytes) else data
    return str(value)


def export_table(cursor, table: str) -> Optional[Dict[str, Any]]:
    """Read every row of one table. Returns None when the table does not exist."""
    if table not in BACKUP_TABLES:
        raise ValueError(f"{table} is not in the backup list")
    try:
        cursor.execute(f"SELECT * FROM {table}")  # table name from the fixed list above only
    except oracledb.Error as error:
        if oracle_code(error) in MISSING_CODES:
            return None
        raise
    columns = [d[0].lower() for d in cursor.description or []]
    rows = [{c: to_jsonable(v) for c, v in zip(columns, row)} for row in cursor.fetchall() or []]
    return {"table": table, "columns": columns, "row_count": len(rows), "rows": rows}


def dumps(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=1)
