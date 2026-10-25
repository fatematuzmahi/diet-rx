"""SCRUM-57: price data + budget modes (SRS 3.3.3.1).

1. Creates the FOOD_PRICE table if it does not exist.
2. Adds four columns to BUDGET, only if they are missing:
       BUDGET_MODE   VARCHAR2(10)   DAILY / WEEKLY / MONTHLY / PLAN
       BUDGET_AMOUNT NUMBER(10,2)   the amount the patient typed
       PLAN_DAYS     NUMBER(3)      only for PLAN budgets
       UPDATED_AT    TIMESTAMP
   MONTHLY_BUDGET keeps working: it is filled with the monthly equivalent, so
   the plan generator (SCRUM-48) and the grocery list (SCRUM-60) need no change.
3. Seeds the Bangladeshi price rows (MERGE on item name + serving unit + market,
   so running it again never makes duplicates and never overwrites an edit).

Safe to run more than once. It never drops, deletes or truncates anything.

Run from the project root:
    python backend/database/apply_food_price_scrum57.py
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.database.connection import get_connection  # noqa: E402
from backend.services.price_service import SEED_PRICES  # noqa: E402

CREATE_FOOD_PRICE = """
CREATE TABLE food_price (
    price_id          NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    item_name         VARCHAR2(150) NOT NULL,
    category          VARCHAR2(40),
    unit              VARCHAR2(50)  NOT NULL,
    price_bdt         NUMBER(10,2)  NOT NULL CHECK (price_bdt > 0),
    market_item       VARCHAR2(150),
    market_unit       VARCHAR2(20),
    market_price_low  NUMBER(10,2),
    market_price_high NUMBER(10,2),
    serving_share     NUMBER(10,4),
    market            VARCHAR2(100),
    source            VARCHAR2(200) NOT NULL,
    source_url        VARCHAR2(400),
    price_date        DATE          NOT NULL,
    season            VARCHAR2(20)  DEFAULT 'ALL_YEAR',
    season_note       VARCHAR2(300),
    created_at        TIMESTAMP     DEFAULT CURRENT_TIMESTAMP,
    updated_at        TIMESTAMP,
    updated_by        VARCHAR2(40)
)
"""

BUDGET_COLUMNS = [
    ("BUDGET_MODE", "VARCHAR2(10)"),
    ("BUDGET_AMOUNT", "NUMBER(10,2)"),
    ("PLAN_DAYS", "NUMBER(3)"),
    ("UPDATED_AT", "TIMESTAMP"),
]

MERGE_PRICE = """
MERGE INTO food_price f
USING (SELECT :item_name AS item_name, :unit AS unit, :market AS market FROM dual) s
ON (UPPER(f.item_name) = UPPER(s.item_name) AND UPPER(f.unit) = UPPER(s.unit)
    AND UPPER(NVL(f.market, '-')) = UPPER(NVL(s.market, '-')))
WHEN NOT MATCHED THEN
  INSERT (item_name, category, unit, price_bdt, market_item, market_unit,
          market_price_low, market_price_high, serving_share, market,
          source, source_url, price_date, season, season_note)
  VALUES (:item_name, :category, :unit, :price_bdt, :market_item, :market_unit,
          :market_price_low, :market_price_high, :serving_share, :market,
          :source, :source_url, :price_date, :season, :season_note)
"""


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
        print("--- FOOD_PRICE ---")
        if columns_of(cur, "FOOD_PRICE"):
            print("  already exists : FOOD_PRICE")
        else:
            cur.execute(CREATE_FOOD_PRICE)
            print("  CREATED        : FOOD_PRICE")

        print("--- BUDGET ---")
        existing = columns_of(cur, "BUDGET")
        if not existing:
            print("  BUDGET table was not found. Budget columns were not added.")
        for column, definition in BUDGET_COLUMNS if existing else []:
            if column in existing:
                print(f"  already exists : BUDGET.{column}")
            else:
                cur.execute(f"ALTER TABLE budget ADD ({column} {definition})")
                print(f"  ADDED          : BUDGET.{column}")

        print("--- Seed prices ---")
        added = 0
        for row in SEED_PRICES:
            cur.execute(MERGE_PRICE, **row)
            added += cur.rowcount or 0
        conn.commit()
        print(f"  {added} new row(s) added, {len(SEED_PRICES) - added} already present.")
        print("\nDone.")
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
