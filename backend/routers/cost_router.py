
"""SCRUM-58: Meal-plan cost estimation API."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import oracledb
from fastapi import APIRouter, Depends, HTTPException, status

from backend.core.security import Principal, require_patient
from backend.database.connection import get_connection
from backend.services import plan_service
from backend.services.cost_estimator import estimate_plan_cost

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/cost", tags=["Cost Estimation"])

_TABLE_MISSING = 942


def _oracle_code(error: oracledb.Error) -> Optional[int]:
    return getattr(error.args[0] if error.args else None, "code", None)


def _patient_id(principal: Principal) -> int:
    try:
        return int(principal.subject)
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication token has an invalid subject.",
        )


def _load_prices(cursor):
    try:
        cursor.execute(
            """
            SELECT item_name, unit, price_bdt, price_date, source
            FROM food_price
            """
        )
    except oracledb.Error as error:
        if _oracle_code(error) == _TABLE_MISSING:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Price data (SCRUM-57) is not set up yet.",
            )
        raise

    return [
        {
            "item_name": row[0],
            "unit": row[1],
            "price_bdt": row[2],
            "price_date": row[3],
            "source": row[4],
        }
        for row in cursor.fetchall() or []
    ]


def _load_budget(cursor, patient_id: int):
    cursor.execute(
        """
        SELECT monthly_budget, preferred_market
        FROM budget
        WHERE patient_id = :patient_id
        """,
        patient_id=patient_id,
    )
    row = cursor.fetchone()
    if row is None:
        return None, None
    return (
        float(row[0]) if row[0] is not None else None,
        row[1],
    )


def _load_plan(cursor, patient_id: int):
    """Prefer the latest accepted plan; otherwise use the current plan."""
    cursor.execute(
        """
        SELECT plan_id
        FROM diet_plan
        WHERE patient_id = :patient_id
          AND accepted_at IS NOT NULL
        ORDER BY accepted_at DESC, plan_id DESC
        """,
        patient_id=patient_id,
    )
    row = cursor.fetchone()
    plan_source = "accepted"

    if row is None:
        row = plan_service.read_current_plan_row(cursor, patient_id)
        if row is None:
            return None, None
        plan_row = row
        plan_source = "current"
    else:
        plan_row = plan_service.read_plan_row(cursor, int(row[0]))

    if plan_row is None:
        return None, None

    from backend.routers.meal_plan_router import _plan_payload

    return _plan_payload(cursor, plan_row), plan_source


@router.get("/estimate")
def read_cost_estimate(
    principal: Principal = Depends(require_patient),
) -> Dict[str, Any]:
    patient_id = _patient_id(principal)
    connection = get_connection()

    if connection is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database connection failed",
        )

    cursor = None
    try:
        cursor = connection.cursor()

        plan, plan_source = _load_plan(cursor, patient_id)
        if plan is None:
            return {
                "found": False,
                "message": "Accept a meal plan first.",
            }

        prices = _load_prices(cursor)
        monthly_budget, preferred_market = _load_budget(cursor, patient_id)

        result = estimate_plan_cost(
            days=plan.get("days", []),
            prices=prices,
            monthly_budget=monthly_budget,
        )

        return {
            "found": True,
            "plan_id": plan["plan_id"],
            "plan_source": plan_source,
            "preferred_market": preferred_market,
            **result,
        }

    except HTTPException:
        raise
    except oracledb.Error as error:
        logger.exception("Oracle error while estimating meal-plan cost")
        if _oracle_code(error) == _TABLE_MISSING:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Required cost-estimation table is not set up yet.",
            )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not calculate meal-plan cost due to a database error.",
        )
    finally:
        if cursor is not None:
            cursor.close()
        connection.close()
