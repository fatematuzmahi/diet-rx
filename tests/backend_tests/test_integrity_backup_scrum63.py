"""SCRUM-63 tests: data integrity checks + backup (SRS 3.11.2).

Runs without Oracle: a small fake cursor answers the read-only queries.
"""

import json
import os
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key")

import oracledb
import pytest

from backend.services import integrity_service as svc

ROOT = Path(__file__).resolve().parents[2]


class MissingTable:
    code = 942


class OtherError:
    code = 1017


class FakeLob:
    def __init__(self, text):
        self.text = text

    def read(self):
        return self.text


class FakeCursor:
    """Answers COUNT checks from ``counts`` (keyed by a word in the SQL) and SELECT * from ``tables``."""

    def __init__(self, counts=None, missing=(), tables=None, fail_with=None):
        self.counts, self.missing, self.tables, self.fail_with = counts or {}, set(missing), tables or {}, fail_with
        self.sql, self.res, self.description = [], [], None

    def execute(self, sql, **binds):
        self.sql.append(sql)
        if self.fail_with:
            raise oracledb.DatabaseError(self.fail_with())
        lowered = sql.lower()
        for name in self.missing:
            if f" {name}" in lowered:
                raise oracledb.DatabaseError(MissingTable())
        if lowered.startswith("select *"):
            table = lowered.split("from ")[1].strip()
            columns, rows = self.tables[table]
            self.description = [(c.upper(),) for c in columns]
            self.res = rows
            return
        self.res = [(next((n for key, n in self.counts.items() if key in lowered), 0),)]

    def fetchone(self):
        return self.res[0] if self.res else None

    def fetchall(self):
        return self.res


# --------------------------------------------------------------------------- #
# Integrity checks
# --------------------------------------------------------------------------- #
def test_every_check_is_read_only():
    for _name, _desc, sql in svc.CHECKS:
        first = sql.strip().split()[0].upper()
        assert first == "SELECT"
        for word in ("DELETE", "UPDATE", "INSERT", "DROP", "TRUNCATE", "ALTER", "MERGE"):
            assert word not in sql.upper().split()


def test_clean_database_is_all_ok():
    results = svc.run_checks(FakeCursor())
    assert len(results) == len(svc.CHECKS)
    assert svc.summarize(results) == {"ok": len(svc.CHECKS), "problem": 0, "skipped": 0}


def test_two_current_plans_is_reported_as_a_problem():
    results = svc.run_checks(FakeCursor(counts={"status = 'current'": 2}))
    row = next(r for r in results if r["name"] == "two_current_plans")
    assert row["status"] == "problem" and row["count"] == 2


def test_price_without_source_is_reported():
    results = svc.run_checks(FakeCursor(counts={"source is null": 3}))
    assert next(r for r in results if r["name"] == "price_without_source")["count"] == 3


def test_missing_table_is_skipped_not_a_failure():
    results = svc.run_checks(FakeCursor(missing={"share_permission", "plan_review"}))
    skipped = {r["name"] for r in results if r["status"] == "skipped"}
    assert {"active_share_with_nothing", "review_without_plan"} <= skipped
    assert svc.summarize(results)["problem"] == 0


def test_a_real_database_error_is_not_hidden():
    with pytest.raises(oracledb.DatabaseError):
        svc.run_checks(FakeCursor(fail_with=OtherError))


# --------------------------------------------------------------------------- #
# Backup
# --------------------------------------------------------------------------- #
def test_values_are_written_as_plain_json():
    assert svc.to_jsonable(Decimal("12")) == 12 and svc.to_jsonable(Decimal("12.50")) == 12.5
    assert svc.to_jsonable(date(2026, 10, 26)) == "2026-10-26"
    assert svc.to_jsonable(datetime(2026, 10, 26, 18, 30)) == "2026-10-26T18:30:00"
    assert svc.to_jsonable(FakeLob('[{"day": 1}]')) == '[{"day": 1}]'
    assert svc.to_jsonable(None) is None


def test_export_reads_every_row_with_column_names():
    cursor = FakeCursor(tables={"food_price": (["item_name", "price_bdt", "price_date"],
                                               [("Rice", Decimal("12.50"), date(2026, 10, 9))])})
    data = svc.export_table(cursor, "food_price")
    assert data["row_count"] == 1 and data["rows"][0] == {"item_name": "Rice", "price_bdt": 12.5,
                                                          "price_date": "2026-10-09"}
    json.loads(svc.dumps(data))  # really valid JSON
    assert cursor.sql == ["SELECT * FROM food_price"]


def test_export_of_a_missing_table_returns_none():
    assert svc.export_table(FakeCursor(missing={"plan_review"}), "plan_review") is None


def test_only_listed_tables_can_be_exported():
    with pytest.raises(ValueError):
        svc.export_table(FakeCursor(), "user_tables; drop table patient")


def test_backup_order_puts_parent_tables_first():
    order = svc.BACKUP_TABLES
    assert order.index("patient") < order.index("health_profile") < order.index("diet_plan") < order.index("plan_review")


def test_backups_folder_is_never_committed():
    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "backups/" in [line.strip() for line in ignored]
