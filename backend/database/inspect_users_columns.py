"""Read and print the Oracle PATIENT table column definitions.

This diagnostic script only queries Oracle's USER_TAB_COLUMNS data dictionary
view; it does not alter database objects or data.
"""

import sys
from pathlib import Path

# Allow direct execution via: python backend/database/inspect_users_columns.py
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.database.connection import get_connection


QUERY = """
SELECT column_name, data_type, nullable
FROM user_tab_columns
WHERE table_name = 'PATIENT'
ORDER BY column_id
"""


def print_patient_columns() -> None:
    """Print column names, data types, and nullability for PATIENT."""
    connection = get_connection()
    if connection is None:
        return

    try:
        with connection.cursor() as cursor:
            cursor.execute(QUERY)
            rows = cursor.fetchall()

        if not rows:
            print("No USERS table columns found for the connected schema.")
            return

        print("PATIENT table columns:")
        for column_name, data_type, nullable in rows:
            print(f"{column_name} | {data_type} | nullable={nullable}")
    finally:
        connection.close()
        print("Database Connection Closed.")


if __name__ == "__main__":
    print_patient_columns()
