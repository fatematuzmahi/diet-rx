"""SCRUM-63: back up the DietRx tables to JSON files on this computer.

READ-ONLY on the database: it only runs SELECT * on the tables in
integrity_service.BACKUP_TABLES and writes one JSON file per table to

    backups/<date>_<time>/<table>.json   (+ manifest.json)

The backups folder is in .gitignore: backups contain personal health data and
password hashes, so they must NEVER be committed or shared.

One connection is used for a few seconds only (the shared freesql database
closes idle connections).

Run from the project root:
    python backend/database/backup_scrum63.py
"""

import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.database.connection import get_connection  # noqa: E402
from backend.services.integrity_service import BACKUP_TABLES, dumps, export_table  # noqa: E402


def main():
    folder = PROJECT_ROOT / "backups" / datetime.now().strftime("%Y%m%d_%H%M%S")
    conn = get_connection()
    if conn is None:
        print("Database connection failed. No backup was made.")
        return 1
    cur = conn.cursor()
    exported = {}
    try:
        for table in BACKUP_TABLES:
            exported[table] = export_table(cur, table)
    finally:
        cur.close()
        conn.close()

    folder.mkdir(parents=True, exist_ok=True)
    manifest = {"created_at": datetime.now().isoformat(timespec="seconds"), "tables": {}}
    for table, data in exported.items():
        if data is None:
            manifest["tables"][table] = "not set up"
            print(f"  skipped : {table} (table not set up)")
            continue
        (folder / f"{table}.json").write_text(dumps(data), encoding="utf-8")
        manifest["tables"][table] = data["row_count"]
        print(f"  saved   : {table:<20} {data['row_count']} rows")
    (folder / "manifest.json").write_text(dumps(manifest), encoding="utf-8")
    print(f"\nDone. Backup saved in: {folder}")
    print("Keep it private: it contains personal health data. Never commit or share it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
