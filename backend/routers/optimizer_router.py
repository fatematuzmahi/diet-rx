"""SCRUM-59: Budget-aware Bengali food optimizer + local alternatives API
(SRS 3.3.3.2, 3.3.3.5).

    GET    /api/optimizer/summary              plan cost vs budget + best swap ideas
    GET    /api/optimizer/alternatives?item=   safe cheaper alternatives for one food
    GET    /api/optimizer/swaps                the patient's saved swaps
    PUT    /api/optimizer/swaps                save a swap (checked again on the server)
    DELETE /api/optimizer/swaps/{swap_id}      undo a swap

Safety comes before cost: every suggestion and every saved swap is checked
against the patient's saved allergies, diet, conditions and prescriptions
(``optimizer_service``). The client can never save an unsafe or more
expensive swap, because the server repeats the check.

Undo never deletes a row: the swap is marked UNDONE, so history stays.
Identity comes only from the access token (require_patient).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import oracledb
from fastapi import APIRouter, Depends, HTTPException, Query, status

from backend.core.security import Principal, require_patient
from backend.database.connection import get_connection
from backend.routers.price_router import PRICE_COLS, _budget_payload, _read_budget
from backend.routers.safety_router import _load_profile
from backend.schemas.optimizer import SwapIn
from backend.services import optimizer_service as opt
from backend.services import plan_service
from backend.services.price_service import row_to_price

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/optimizer", tags=["Budget Optimizer"])

SETUP_HINT = "Smart swaps are not set up yet. Run: python backend/database/apply_meal_swap_scrum59.py"
NO_PLAN = "Accept your meal plan first, then DietRx can look for cheaper swaps."


def _code(error: oracledb.Error) -> Optional[int]:
    return getattr(error.args[0] if error.args else None, "code", None)


def _patient_pk(principal: Principal) -> int:
    try:
        return int(principal.subject)
    except (TypeError, ValueError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Authentication token has an invalid subject.")


def _connect():
    connection = get_connection()
    if connection is None:
        raise HTTPException(status_code=503, detail="Database connection failed")
    return connection


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _fail(error: oracledb.Error, action: str) -> HTTPException:
    logger.error("SCRUM-59 %s failed: %s", action, error)
    if _code(error) in (942, 904):
        return HTTPException(status_code=503, detail=SETUP_HINT)
    return HTTPException(status_code=500, detail=f"Unable to {action} right now.")


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #
def load_prices(cursor) -> List[Dict[str, Any]]:
    try:
        cursor.execute(f"SELECT {PRICE_COLS} FROM food_price")
    except oracledb.Error as error:
        if _code(error) in (942, 904):
            return []  # SCRUM-57 not applied: no prices, so no suggestions (never guessed)
        raise
    return [row_to_price(r) for r in cursor.fetchall() or []]


def load_context(cursor, patient_id: int) -> Dict[str, Any]:
    profile = _load_profile(cursor, patient_id)
    prescriptions = plan_service.read_prescription_rows(cursor, patient_id)
    return opt.build_context(profile, prescriptions)


def load_swap_rows(cursor, patient_id: int, missing_ok: bool = True) -> List[Dict[str, Any]]:
    try:
        cursor.execute(
            "SELECT swap_id, from_item, to_item, servings_ratio, created_at FROM meal_swap "
            "WHERE patient_id = :patient_id AND status = 'ACTIVE' ORDER BY swap_id",
            patient_id=patient_id,
        )
    except oracledb.Error as error:
        if missing_ok and _code(error) in (942, 904):
            return []
        raise
    return [{"swap_id": int(r[0]), "from_item": r[1], "to_item": r[2],
             "servings_ratio": float(r[3]) if r[3] is not None else None, "created_at": r[4]}
            for r in cursor.fetchall() or []]


def _accepted_plan_days(cursor, patient_id: int) -> Optional[Dict[str, Any]]:
    cursor.execute(
        "SELECT plan_id, accepted_at FROM diet_plan "
        "WHERE patient_id = :patient_id AND accepted_at IS NOT NULL "
        "ORDER BY accepted_at DESC, plan_id DESC",
        patient_id=patient_id,
    )
    latest = cursor.fetchone()
    if latest is None:
        return None
    row = plan_service.read_plan_row(cursor, int(latest[0]))
    if row is None:
        return None
    plan = plan_service.row_to_plan(cursor, row)
    days = [d.model_dump() if hasattr(d, "model_dump") else d for d in plan.get("days", [])]
    return {"plan_id": plan.get("plan_id"), "accepted_at": latest[1], "days": days}


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #
@router.get("/summary")
def read_summary(principal: Principal = Depends(require_patient)):
    patient_id = _patient_pk(principal)
    connection = _connect()
    cursor = connection.cursor()
    try:
        plan = _accepted_plan_days(cursor, patient_id)
        if plan is None:
            return {"found": False, "message": NO_PLAN, "foods": opt.food_choices()}
        prices = load_prices(cursor)
        ctx = load_context(cursor, patient_id)
        swaps = load_swap_rows(cursor, patient_id)
        budget = _budget_payload(_read_budget(cursor, patient_id))
    except oracledb.Error as error:
        raise _fail(error, "check your plan for savings")
    finally:
        cursor.close()
        connection.close()

    summary = opt.build_summary(plan["days"], prices, ctx, budget, swaps)
    return {"found": True, "plan_id": plan["plan_id"], "accepted_at": plan["accepted_at"],
            "prices_available": bool(prices), "foods": opt.food_choices(), **summary}


@router.get("/alternatives")
def read_alternatives(item: str = Query(..., min_length=1, max_length=60),
                      principal: Principal = Depends(require_patient)):
    key = opt.resolve_key(item)
    if key is None:
        raise HTTPException(status_code=404, detail="DietRx has no swap information for this food yet.")
    patient_id = _patient_pk(principal)
    connection = _connect()
    cursor = connection.cursor()
    try:
        prices = load_prices(cursor)
        ctx = load_context(cursor, patient_id)
    except oracledb.Error as error:
        raise _fail(error, "find alternatives")
    finally:
        cursor.close()
        connection.close()
    result = opt.alternatives_for(key, ctx, prices)
    return {**result, "checked_against": ctx["checked_against"], "disclaimer": opt.DISCLAIMER}


@router.get("/swaps")
def read_swaps(principal: Principal = Depends(require_patient)):
    patient_id = _patient_pk(principal)
    connection = _connect()
    cursor = connection.cursor()
    try:
        rows = load_swap_rows(cursor, patient_id)
        prices = load_prices(cursor) if rows else []
        ctx = load_context(cursor, patient_id) if rows else None
    except oracledb.Error as error:
        raise _fail(error, "load your swaps")
    finally:
        cursor.close()
        connection.close()
    if not rows:
        return {"active_swaps": [], "paused_swaps": []}
    _map, active, paused = opt.grocery_swaps(rows, ctx, prices)
    return {"active_swaps": active, "paused_swaps": paused}


@router.put("/swaps")
def save_swap(body: SwapIn, principal: Principal = Depends(require_patient)):
    patient_id = _patient_pk(principal)
    from_key, to_key = opt.resolve_key(body.from_item), opt.resolve_key(body.to_item)
    if from_key is None or to_key is None:
        raise HTTPException(status_code=400, detail="That food cannot be swapped.")
    connection = _connect()
    cursor = connection.cursor()
    try:
        prices = load_prices(cursor)
        ctx = load_context(cursor, patient_id)
        try:
            alt = opt.validate_swap(from_key, to_key, ctx, prices)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error))
        reason = " ".join(alt["why"][:2])[:400]
        cursor.execute(
            "SELECT swap_id FROM meal_swap WHERE patient_id = :patient_id "
            "AND from_item = :from_item AND status = 'ACTIVE'",
            patient_id=patient_id, from_item=from_key,
        )
        existing = cursor.fetchone()
        if existing:
            swap_id = int(existing[0])
            cursor.execute(
                "UPDATE meal_swap SET to_item = :to_item, servings_ratio = :ratio, reason = :reason, "
                "updated_at = :updated_at WHERE swap_id = :swap_id AND patient_id = :patient_id",
                to_item=to_key, ratio=alt["servings_ratio"], reason=reason, updated_at=_now(),
                swap_id=swap_id, patient_id=patient_id,
            )
        else:
            new_id = cursor.var(int)
            cursor.execute(
                "INSERT INTO meal_swap (patient_id, from_item, to_item, servings_ratio, reason, status) "
                "VALUES (:patient_id, :from_item, :to_item, :ratio, :reason, 'ACTIVE') "
                "RETURNING swap_id INTO :new_id",
                patient_id=patient_id, from_item=from_key, to_item=to_key,
                ratio=alt["servings_ratio"], reason=reason, new_id=new_id,
            )
            swap_id = int(new_id.getvalue()[0])
        connection.commit()
    except HTTPException:
        connection.rollback()
        raise
    except oracledb.Error as error:
        connection.rollback()
        raise _fail(error, "save your swap")
    finally:
        cursor.close()
        connection.close()
    return {
        "swap_id": swap_id,
        "from_key": from_key, "from_name": opt.GROCERY_ITEMS[from_key][0],
        "to_key": to_key, "to_name": opt.GROCERY_ITEMS[to_key][0],
        "portion": alt["portion"], "saving_per_serving": alt["saving_per_serving"],
        "message": f"Saved. Your grocery list now uses {opt.GROCERY_ITEMS[to_key][0].lower()} "
                   f"instead of {opt.short_name(from_key)}.",
    }


@router.delete("/swaps/{swap_id}")
def undo_swap(swap_id: int, principal: Principal = Depends(require_patient)):
    patient_id = _patient_pk(principal)
    connection = _connect()
    cursor = connection.cursor()
    try:
        cursor.execute(
            "UPDATE meal_swap SET status = 'UNDONE', updated_at = :updated_at "
            "WHERE swap_id = :swap_id AND patient_id = :patient_id AND status = 'ACTIVE'",
            updated_at=_now(), swap_id=swap_id, patient_id=patient_id,
        )
        if not cursor.rowcount:
            raise HTTPException(status_code=404, detail="That swap was not found.")
        connection.commit()
    except HTTPException:
        connection.rollback()
        raise
    except oracledb.Error as error:
        connection.rollback()
        raise _fail(error, "undo your swap")
    finally:
        cursor.close()
        connection.close()
    return {"swap_id": swap_id, "undone": True, "message": "Swap undone. Your grocery list is back to the plan."}
