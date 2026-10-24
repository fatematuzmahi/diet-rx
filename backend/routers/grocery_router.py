"""SCRUM-60: Grocery list + purchase links API (SRS 3.3.3.3, 3.3.3.4).

    GET /api/grocery/list

* Built from the patient's latest ACCEPTED meal plan (SCRUM-49), so the list
  updates by itself as soon as a new plan is accepted.
* Costs use the FOOD_PRICE table (SCRUM-57). If that table is not there yet,
  the list still works and simply says prices are not available.
* The monthly budget comes from the existing BUDGET table.
* A copy of the list is saved in the existing GROCERY_LIST table (one row per
  accepted plan; a new plan adds a new row). If that table is missing the
  list is still returned. Nothing is ever deleted.

Identity comes only from the access token (require_patient).
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

import oracledb
from fastapi import APIRouter, Depends, HTTPException, status

from backend.core.security import Principal, require_patient
from backend.database.connection import get_connection
from backend.services import plan_service
from backend.services.grocery_service import build_grocery_list

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/grocery", tags=["Grocery"])

_TABLE_MISSING = 942  # ORA-00942: table or view does not exist


def _code(error: oracledb.Error) -> Optional[int]:
    return getattr(error.args[0] if error.args else None, "code", None)


def _patient_pk(principal: Principal) -> int:
    try:
        return int(principal.subject)
    except (TypeError, ValueError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Authentication token has an invalid subject.")


def _latest_accepted(cursor, patient_id: int):
    cursor.execute(
        "SELECT plan_id, accepted_at FROM diet_plan "
        "WHERE patient_id = :patient_id AND accepted_at IS NOT NULL "
        "ORDER BY accepted_at DESC, plan_id DESC",
        patient_id=patient_id,
    )
    return cursor.fetchone()


def _load_prices(cursor) -> List[Dict[str, Any]]:
    try:
        cursor.execute("SELECT item_name, unit, price_bdt, price_date FROM food_price")
    except oracledb.Error as error:
        if _code(error) == _TABLE_MISSING:
            return []  # SCRUM-57 not applied yet: no prices, no guessing
        raise
    return [
        {"item_name": r[0], "unit": r[1], "price_bdt": r[2], "price_date": r[3]}
        for r in cursor.fetchall() or []
    ]


def _load_budget(cursor, patient_id: int):
    cursor.execute(
        "SELECT budget_id, monthly_budget FROM budget WHERE patient_id = :patient_id "
        "ORDER BY budget_id DESC",
        patient_id=patient_id,
    )
    row = cursor.fetchone()
    return (row[0], float(row[1]) if row[1] is not None else None) if row else (None, None)


def _save_snapshot(connection, cursor, patient_id: int, plan_id: int,
                   budget_id, result: Dict[str, Any]) -> Dict[str, Any]:
    """Keep one GROCERY_LIST row per accepted plan. Never deletes anything."""
    items = [
        {"name": i["name"], "servings": i["servings"], "cost_bdt": i["estimated_cost_bdt"]}
        for c in result["categories"] for i in c["items"]
    ]
    total = result["summary"]["total_bdt"]
    quantity = f"{result['item_count']} items / {result['plan_days']} days"
    try:
        cursor.execute(
            "SELECT list_id, plan_id FROM grocery_list WHERE patient_id = :patient_id "
            "ORDER BY list_id DESC FETCH FIRST 1 ROWS ONLY",
            patient_id=patient_id,
        )
        last = cursor.fetchone()
        if last is not None and int(last[1]) == plan_id:
            cursor.execute(
                "UPDATE grocery_list SET items = :items, quantity = :quantity, "
                "total_cost = :total_cost, budget_id = :budget_id WHERE list_id = :list_id",
                items=json.dumps(items), quantity=quantity, total_cost=total,
                budget_id=budget_id, list_id=int(last[0]),
            )
            changed = False
        else:
            cursor.execute(
                "INSERT INTO grocery_list (patient_id, plan_id, budget_id, items, quantity, total_cost) "
                "VALUES (:patient_id, :plan_id, :budget_id, :items, :quantity, :total_cost)",
                patient_id=patient_id, plan_id=plan_id, budget_id=budget_id,
                items=json.dumps(items), quantity=quantity, total_cost=total,
            )
            changed = last is not None  # an older plan's list existed
        connection.commit()
        return {"saved": True, "updated_for_new_plan": changed}
    except oracledb.Error as error:
        connection.rollback()
        logger.warning("SCRUM-60 grocery snapshot not saved: %s", error)
        return {"saved": False, "updated_for_new_plan": False}


@router.get("/list")
def read_grocery_list(principal: Principal = Depends(require_patient)):
    patient_id = _patient_pk(principal)
    connection = get_connection()
    if connection is None:
        raise HTTPException(status_code=503, detail="Database connection failed")
    cursor = connection.cursor()
    try:
        latest = _latest_accepted(cursor, patient_id)
        if latest is None:
            return {"found": False, "message": "Accept your meal plan first to get a grocery list."}
        row = plan_service.read_plan_row(cursor, int(latest[0]))
        if row is None:
            return {"found": False, "message": "Accept your meal plan first to get a grocery list."}
        plan = plan_service.row_to_plan(cursor, row)
        days = [d.model_dump() if hasattr(d, "model_dump") else d for d in plan.get("days", [])]

        prices = _load_prices(cursor)
        budget_id, monthly_budget = _load_budget(cursor, patient_id)
        result = build_grocery_list(days, prices, monthly_budget)
        saved = _save_snapshot(connection, cursor, patient_id, int(plan["plan_id"]), budget_id, result)

        return {
            "found": True,
            "plan_id": plan["plan_id"],
            "accepted_at": latest[1],
            **result,
            **saved,
        }
    except HTTPException:
        raise
    except oracledb.Error as error:
        logger.error("SCRUM-60 grocery list failed: %s", error)
        if _code(error) == 904:
            raise HTTPException(status_code=503, detail="Meal plan acceptance is not set up yet (SCRUM-49).")
        raise HTTPException(status_code=500, detail="Unable to build your grocery list right now.")
    finally:
        cursor.close()
        connection.close()
