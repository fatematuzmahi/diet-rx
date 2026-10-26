"""SCRUM-63: check the shared database for data-integrity problems.

READ-ONLY: it only runs SELECT COUNT(*) queries and prints a report.
It never changes, fixes or deletes anything.

Run from the project root:
    python backend/database/check_integrity_scrum63.py
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.database.connection import get_connection  # noqa: E402
from backend.services.integrity_service import run_checks, summarize  # noqa: E402

MARK = {"ok": "OK     ", "problem": "PROBLEM", "skipped": "skipped"}


def main():
    conn = get_connection()
    if conn is None:
        print("Database connection failed. Nothing was checked.")
        return 1
    cur = conn.cursor()
    try:
        results = run_checks(cur)
    finally:
        cur.close()
        conn.close()

    print("\nDietRx data integrity check (read-only)\n")
    for r in results:
        count = "" if r["count"] is None else f"  ({r['count']} rows)"
        print(f"  [{MARK[r['status']]}] {r['description']}{count}")
    totals = summarize(results)
    print(f"\n{totals['ok']} ok, {totals['problem']} problem, {totals['skipped']} skipped (table not set up)")
    return 1 if totals["problem"] else 0


if __name__ == "__main__":
    sys.exit(main())
