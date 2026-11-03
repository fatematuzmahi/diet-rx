"""SCRUM-52: Food & Activity Safety Checker API (SRS 3.2.3.1).

    POST /api/safety/check

The page sends what the user typed (food, allergies, conditions, restrictions,
portion_limits, preparation_info). This route adds the patient's SAVED health
profile and the matching Dish DNA record, then asks the pure rule engine in
``backend.services.food_checker`` for Safe / Caution / Avoid with reasons.

Identity comes only from the access token (require_patient); no patient id is
accepted from the request. Read-only: no table is created or changed.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Union

import oracledb
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from backend.core.security import Principal, require_patient
from backend.database.connection import get_connection
from backend.services.food_checker import check_food_safety

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/safety", tags=["Safety Checker"])


class SafetyCheckRequest(BaseModel):
    food: str = Field(default="", max_length=150)
    allergies: Union[List[str], str, None] = None
    conditions: Union[List[str], str, None] = None
    restrictions: Union[List[str], str, None] = None
    portion_limits: Union[Dict[str, Any], str, None] = None
    preparation_info: Optional[str] = Field(default=None, max_length=300)


def _text(value: Any) -> Optional[str]:
    """VARCHAR2 or CLOB -> str."""
    if value is None:
        return None
    read = getattr(value, "read", None)
    return str(read()) if callable(read) else str(value)


def _load_profile(cursor, patient_id: int) -> Dict[str, Any]:
    cursor.execute(
        """
        SELECT allergies, conditions, food_preference
        FROM health_profile
        WHERE patient_id = :patient_id
        """,
        patient_id=patient_id,
    )
    row = cursor.fetchone()
    if not row:
        return {}
    return {
        "allergies": _text(row[0]),
        "conditions": _text(row[1]),
        "food_preference": _text(row[2]),
    }


def _find_dish(cursor, food: str) -> Optional[Dict[str, Any]]:
    """Exact name first, then the shortest name that contains the typed text."""
    cursor.execute(
        """
        SELECT name, ingredients, allergens, diet_tags,
               sugar_g, glycemic_index, sodium_mg, saturated_fat_g
        FROM dish_dna
        WHERE UPPER(name) = UPPER(:food_name)
           OR UPPER(name) LIKE '%' || UPPER(:food_name) || '%'
        ORDER BY CASE WHEN UPPER(name) = UPPER(:food_name) THEN 0 ELSE 1 END,
                 LENGTH(name)
        FETCH FIRST 1 ROWS ONLY
        """,
        food_name=food,
    )
    row = cursor.fetchone()
    if not row:
        return None
    keys = ("name", "ingredients", "allergens", "diet_tags",
            "sugar_g", "glycemic_index", "sodium_mg", "saturated_fat_g")
    return {k: (_text(v) if k in ("name", "ingredients", "allergens", "diet_tags") else v)
            for k, v in zip(keys, row)}


@router.post("/check")
def check_food(
    body: SafetyCheckRequest,
    principal: Principal = Depends(require_patient),
):
    payload = body.model_dump() if hasattr(body, "model_dump") else body.dict()
    food = (payload.get("food") or "").strip()
    if not food:
        return check_food_safety(payload)  # status "error", food name required

    try:
        patient_id = int(principal.subject)
    except (TypeError, ValueError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Authentication token has an invalid subject.")

    connection = get_connection()
    if connection is None:
        raise HTTPException(status_code=503, detail="Database connection failed")
    cursor = connection.cursor()
    try:
        profile = _load_profile(cursor, patient_id)
        dish = _find_dish(cursor, food)
    except oracledb.Error as error:
        logger.error("SCRUM-52 safety check failed: %s", error)
        raise HTTPException(status_code=500, detail="Unable to check this food right now.")
    finally:
        cursor.close()
        connection.close()

    return check_food_safety(payload, profile=profile, dish=dish)