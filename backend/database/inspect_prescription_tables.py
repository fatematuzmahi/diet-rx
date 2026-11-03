"""SCRUM-46 (read-only) live schema inspection for PATIENT, DOCTOR, PRESCRIPTION.

This diagnostic script ONLY reads Oracle's data dictionary (USER_TAB_COLUMNS,
USER_CONSTRAINTS, USER_CONS_COLUMNS) and per-table COUNT(*). It never SELECTs
column data from any business table, so no patient-identifiable, credential, or
otherwise sensitive values are read or printed. It performs no DDL/DML.
"""

import sys
from pathlib import Path

# Allow direct execution via: python backend/database/inspect_prescription_tables.py
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.database.connection import get_connection

TABLES = ("PATIENT", "DOCTOR", "PRESCRIPTION")

COLS_SQL = """
SELECT table_name, column_name, data_type, data_length, data_precision,
       data_scale, nullable, data_default
FROM user_tab_columns
WHERE table_name = :t
ORDER BY column_id
"""

CONS_SQL = """
SELECT c.constraint_name, c.constraint_type, c.search_condition,
       cc.column_name, cc.position
FROM user_constraints c
LEFT JOIN user_cons_columns cc
       ON c.constraint_name = cc.constraint_name
WHERE c.table_name = :t
ORDER BY c.constraint_type, c.constraint_name, cc.position
"""

COUNT_SQL = "SELECT COUNT(*) FROM {table}"


def inspect(connection):
    with connection.cursor() as cursor:
        for table in TABLES:
            print(f"\n===== TABLE: {table} =====")

            cursor.execute(COLS_SQL, t=table)
            cols = cursor.fetchall()
            if not cols:
                print("  (table not found in connected schema)")
            for (_t, name, dtype, dlen, dprec, dscale, nullable, default) in cols:
                print(
                    f"  COL {name:22} {dtype:12} "
                    f"len={dlen} prec={dprec} scale={dscale} "
                    f"nullable={nullable} default={default}"
                )

            print("  -- constraints --")
            cursor.execute(CONS_SQL, t=table)
            cons = cursor.fetchall()
            if not cons:
                print("  (none)")
            for (cname, ctype, search, col, pos) in cons:
                # R=PK, P=PK, U=UNIQUE, C=CHECK, R/F=FOREIGN KEY
                label = {"P": "PRIMARY", "U": "UNIQUE", "C": "CHECK",
                         "R": "FK", "V": "CHECK-VIEW"}.get(ctype, ctype)
                detail = f" on {col}" if col else ""
                if ctype == "C" and search:
                    detail += f" cond={search}"
                print(f"    [{label}] {cname}{detail}")

            print("  -- row count --")
            # Count only; never reads any column values.
            cursor.execute(COUNT_SQL.format(table=table))
            print(f"    rows = {cursor.fetchone()[0]}")


def main():
    connection = get_connection()
    if connection is None:
        print("No live Oracle connection available; cannot inspect tables.")
        return
    try:
        inspect(connection)
    finally:
        connection.close()
        print("\nDatabase Connection Closed.")


if __name__ == "__main__":
    main()
