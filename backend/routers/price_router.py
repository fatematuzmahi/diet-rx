"""
SCRUM-57: price data + budget API (SRS 3.3.3.1).

GET  /api/prices                 price list (any signed-in user)
POST /api/prices                 add a price (doctor/admin)
PUT  /api/prices/{price_id}      correct a price (doctor/admin)
GET  /api/budget/me              the patient's budget
PUT  /api/budget/me              set the budget (daily / weekly / monthly / plan)

There is no DELETE: a wrong price is corrected with PUT, so history stays
traceable. Budget identity comes only from the token.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import oracledb
from fastapi import APIRouter, Depends, HTTPException, Query, status

from backend.core.security import (
    Principal,
    require_authenticated,
    require_doctor_or_admin,
    require_patient,
)
from backend.database.connection import get_connection
from backend.schemas.price import BudgetIn, PriceIn
from backend.services.price_service import DISCLAIMER, normalise_budget, row_to_price

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Prices & Budget"])

SETUP_HINT = (
    "Price data is not set up yet. "
    "Run: python backend/database/apply_food_price_scrum57.py"
)

PRICE_COLS = (
    "price_id, item_name, category, unit, price_bdt, market_item, market_unit, "
    "market_price_low, market_price_high, serving_share, market, source, source_url, "
    "price_date, season, season_note, updated_at"
)


def _code(error: oracledb.Error) -> Optional[int]:
    return getattr(error.args[0] if error.args else None, "code", None)


def _connect():
    connection = get_connection()
    if connection is None:
        raise HTTPException(
            status_code=503,
            detail="Database connection failed",
        )
    return connection


def _fail(error: oracledb.Error, action: str) -> HTTPException:
    logger.error("SCRUM-57 %s failed: %s", action, error)

    if _code(error) in (942, 904):
        return HTTPException(
            status_code=503,
            detail=SETUP_HINT,
        )

    return HTTPException(
        status_code=500,
        detail=f"Unable to {action} right now.",
    )


def _patient_pk(principal: Principal) -> int:
    try:
        return int(principal.subject)
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication token has an invalid subject.",
        )


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _escape_like(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )


# ---------------------------------------------------------------------------
# Prices
# ---------------------------------------------------------------------------

@router.get("/api/prices")
def list_prices(
    search: Optional[str] = Query(default=None, max_length=100),
    category: Optional[str] = Query(default=None, max_length=40),
    principal: Principal = Depends(require_authenticated),
):
    where, binds = [], {}

    if search and search.strip():
        where.append(
            "(UPPER(item_name) LIKE :q ESCAPE '\\' "
            "OR UPPER(market_item) LIKE :q ESCAPE '\\')"
        )
        binds["q"] = f"%{_escape_like(search.strip().upper())}%"

    if category and category.strip():
        where.append("UPPER(category) = :category")
        binds["category"] = category.strip().upper()

    sql = f"SELECT {PRICE_COLS} FROM food_price"

    if where:
        sql += " WHERE " + " AND ".join(where)

    sql += " ORDER BY category, item_name"

    connection = _connect()
    cursor = connection.cursor()

    try:
        cursor.execute(sql, **binds)
        prices = [row_to_price(row) for row in cursor.fetchall() or []]

    except oracledb.Error as error:
        raise _fail(error, "load prices")

    finally:
        cursor.close()
        connection.close()

    dates = [price["price_date"] for price in prices if price["price_date"]]

    sources: Dict[str, Dict[str, Any]] = {}

    for price in prices:
        sources.setdefault(
            price["source"],
            {
                "source": price["source"],
                "source_url": price["source_url"],
                "price_date": price["price_date"],
            },
        )

    return {
        "prices": prices,
        "total": len(prices),
        "categories": sorted(
            {price["category"] for price in prices if price["category"]}
        ),
        "oldest_price_date": min(dates) if dates else None,
        "newest_price_date": max(dates) if dates else None,
        "stale_count": sum(1 for price in prices if price["stale"]),
        "sources": list(sources.values()),
        "disclaimer": DISCLAIMER,
    }


def _duplicate(
    cursor,
    body: PriceIn,
    exclude_id: Optional[int] = None,
) -> bool:
    sql = (
        "SELECT price_id FROM food_price "
        "WHERE UPPER(item_name) = UPPER(:item_name) "
        "AND UPPER(unit) = UPPER(:unit) "
        "AND UPPER(NVL(market, '-')) = UPPER(NVL(:market, '-'))"
    )

    binds = {
        "item_name": body.item_name,
        "unit": body.unit,
        "market": body.market,
    }

    if exclude_id is not None:
        sql += " AND price_id <> :price_id"
        binds["price_id"] = exclude_id

    cursor.execute(sql, **binds)
    return cursor.fetchone() is not None


def _write_binds(body: PriceIn) -> Dict[str, Any]:
    return {
        "item_name": body.item_name,
        "category": body.category,
        "unit": body.unit,
        "price_bdt": body.price_bdt,
        "market": body.market,
        "source": body.source,
        "source_url": body.source_url,
        "price_date": body.price_date,
        "season": body.season,
        "season_note": body.season_note,
    }


@router.post("/api/prices", status_code=201)
def add_price(
    body: PriceIn,
    principal: Principal = Depends(require_doctor_or_admin),
):
    connection = _connect()
    cursor = connection.cursor()

    try:
        if _duplicate(cursor, body):
            raise HTTPException(
                status_code=409,
                detail="This price already exists. Edit it instead.",
            )

        new_id = cursor.var(int)

        cursor.execute(
            "INSERT INTO food_price "
            "(item_name, category, unit, price_bdt, market, source, source_url, "
            "price_date, season, season_note, updated_at, updated_by) "
            "VALUES (:item_name, :category, :unit, :price_bdt, :market, :source, "
            ":source_url, :price_date, :season, :season_note, :updated_at, "
            ":updated_by) RETURNING price_id INTO :new_id",
            **_write_binds(body),
            updated_at=_now(),
            updated_by=f"{principal.role}:{principal.subject}",
            new_id=new_id,
        )

        price_id = int(new_id.getvalue()[0])
        connection.commit()

        cursor.execute(
            f"SELECT {PRICE_COLS} FROM food_price WHERE price_id = :price_id",
            price_id=price_id,
        )

        return row_to_price(cursor.fetchone())

    except HTTPException:
        connection.rollback()
        raise

    except oracledb.Error as error:
        connection.rollback()
        raise _fail(error, "save the price")

    finally:
        cursor.close()
        connection.close()


@router.put("/api/prices/{price_id}")
def update_price(
    price_id: int,
    body: PriceIn,
    principal: Principal = Depends(require_doctor_or_admin),
):
    connection = _connect()
    cursor = connection.cursor()

    try:
        if _duplicate(cursor, body, exclude_id=price_id):
            raise HTTPException(
                status_code=409,
                detail="Another row already has this item, unit and market.",
            )

        cursor.execute(
            "UPDATE food_price SET "
            "item_name = :item_name, category = :category, unit = :unit, "
            "price_bdt = :price_bdt, market = :market, source = :source, "
            "source_url = :source_url, price_date = :price_date, "
            "season = :season, season_note = :season_note, "
            "updated_at = :updated_at, updated_by = :updated_by "
            "WHERE price_id = :price_id",
            **_write_binds(body),
            updated_at=_now(),
            updated_by=f"{principal.role}:{principal.subject}",
            price_id=price_id,
        )

        if not cursor.rowcount:
            raise HTTPException(
                status_code=404,
                detail="That price was not found.",
            )

        connection.commit()

        cursor.execute(
            f"SELECT {PRICE_COLS} FROM food_price WHERE price_id = :price_id",
            price_id=price_id,
        )

        return row_to_price(cursor.fetchone())

    except HTTPException:
        connection.rollback()
        raise

    except oracledb.Error as error:
        connection.rollback()
        raise _fail(error, "update the price")

    finally:
        cursor.close()
        connection.close()


# ---------------------------------------------------------------------------
# Budget
# ---------------------------------------------------------------------------

def _budget_payload(row) -> Dict[str, Any]:
    if row is None:
        return {
            "found": False,
            "mode": None,
            "amount": None,
            "plan_days": None,
            "preferred_market": None,
            "per_day": None,
            "per_week": None,
            "per_month": None,
            "updated_at": None,
        }

    budget_id, monthly, market, mode, amount, plan_days, updated_at = row

    if not mode and monthly is not None:
        # Budget saved before SCRUM-57.
        mode, amount = "MONTHLY", monthly

    payload = {
        "found": amount is not None,
        "mode": mode,
        "amount": float(amount) if amount is not None else None,
        "plan_days": int(plan_days) if plan_days is not None else None,
        "preferred_market": market,
        "updated_at": updated_at,
    }

    if amount is not None:
        payload.update(
            normalise_budget(mode, float(amount), payload["plan_days"])
        )
    else:
        payload.update(
            {
                "per_day": None,
                "per_week": None,
                "per_month": None,
            }
        )

    return payload


def _read_budget(cursor, patient_id: int):
    cursor.execute(
        "SELECT budget_id, monthly_budget, preferred_market, budget_mode, "
        "budget_amount, plan_days, updated_at "
        "FROM budget WHERE patient_id = :patient_id ORDER BY budget_id DESC",
        patient_id=patient_id,
    )
    return cursor.fetchone()


@router.get("/api/budget/me")
def read_my_budget(
    principal: Principal = Depends(require_patient),
):
    patient_id = _patient_pk(principal)
    connection = _connect()
    cursor = connection.cursor()

    try:
        return _budget_payload(_read_budget(cursor, patient_id))

    except oracledb.Error as error:
        raise _fail(error, "load your budget")

    finally:
        cursor.close()
        connection.close()


@router.put("/api/budget/me")
def save_my_budget(
    body: BudgetIn,
    principal: Principal = Depends(require_patient),
):
    patient_id = _patient_pk(principal)
    totals = normalise_budget(body.mode, body.amount, body.plan_days)

    binds = {
        "monthly_budget": totals["per_month"],
        "preferred_market": body.preferred_market,
        "budget_mode": body.mode,
        "budget_amount": body.amount,
        "plan_days": body.plan_days,
        "updated_at": _now(),
        "patient_id": patient_id,
    }

    connection = _connect()
    cursor = connection.cursor()

    try:
        existing = _read_budget(cursor, patient_id)

        if existing is None:
            cursor.execute(
                "INSERT INTO budget "
                "(patient_id, monthly_budget, preferred_market, budget_mode, "
                "budget_amount, plan_days, updated_at) "
                "VALUES (:patient_id, :monthly_budget, :preferred_market, "
                ":budget_mode, :budget_amount, :plan_days, :updated_at)",
                **binds,
            )
        else:
            cursor.execute(
                "UPDATE budget SET "
                "monthly_budget = :monthly_budget, "
                "preferred_market = :preferred_market, "
                "budget_mode = :budget_mode, "
                "budget_amount = :budget_amount, "
                "plan_days = :plan_days, updated_at = :updated_at "
                "WHERE budget_id = :budget_id AND patient_id = :patient_id",
                **binds,
                budget_id=int(existing[0]),
            )

        connection.commit()
        return _budget_payload(_read_budget(cursor, patient_id))

    except oracledb.Error as error:
        connection.rollback()
        raise _fail(error, "save your budget")

    finally:
        cursor.close()
        connection.close()

