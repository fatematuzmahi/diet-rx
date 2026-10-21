"""SCRUM-47: Dish DNA endpoints -- the nutritional fingerprint of a dish.

Endpoints
---------
    GET    /api/dish-dna              list + search + filter (paginated)
    GET    /api/dish-dna/{dish_id}    read one dish
    GET    /api/dish-dna/facets       distinct cuisines / categories / diet tags
    POST   /api/dish-dna              create a dish (DOCTOR only)
    PUT    /api/dish-dna/{dish_id}    update a dish (DOCTOR only)
    DELETE /api/dish-dna/{dish_id}    delete a dish (DOCTOR only)

Design notes
------------
* DISH_ID is ``GENERATED ALWAYS AS IDENTITY``. The INSERT therefore NEVER names
  the column and NEVER references a sequence (that would raise ORA-02289 /
  ORA-32795). The generated key is read back with
  ``RETURNING dish_id INTO :new_id`` and taken as ``getvalue()[0]`` because
  ``cursor.var(int).getvalue()`` returns a one-element LIST.
* Read access is open to any authenticated principal (patients need the data to
  make diet decisions); writes are restricted to DOCTORs, because the Dish DNA
  is curated clinical reference data. A PATIENT calling a write endpoint gets
  403 from the shared SCRUM-44 security layer.
* The dynamic health-status badges ("High Protein", "Low Sodium", ...) are
  DERIVED server-side from the stored nutrients by
  ``backend.schemas.dish_dna.derive_health_statuses``, so every client renders
  the same verdict for the same dish.
* Oracle ``NUMBER`` columns are returned as ``Decimal``; they are converted to
  ``float`` so the JSON payload carries plain numbers.
* Every response carries the SRS decision-support disclaimer.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

import oracledb
from fastapi import APIRouter, Depends, HTTPException, Query, status

from backend.core.security import Principal, require_authenticated, require_doctor
from backend.database.connection import get_connection
from backend.schemas.dish_dna import (
    DECISION_SUPPORT_DISCLAIMER,
    DishDnaCreate,
    DishDnaFilters,
    DishDnaListResponse,
    DishDnaResponse,
    DishDnaUpdate,
    _split_tags,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/dish-dna", tags=["Dish DNA"])

# Column order MUST match the unpacking in DishDnaResponse.build().
_COLS = (
    "dish_id, name, cuisine, category, description, ingredients, "
    "serving_size_g, serving_description, "
    "calories, protein_g, carbs_g, fat_g, fiber_g, sugar_g, "
    "glycemic_index, sodium_mg, saturated_fat_g, "
    "vitamin_a_mcg, vitamin_c_mg, vitamin_d_mcg, vitamin_k_mcg, b_vitamin_mg, "
    "calcium_mg, iron_mg, potassium_mg, "
    "diet_tags, allergens, created_at, updated_at"
)

#: Writable columns, in the order used by the INSERT statement.
_WRITABLE_COLUMNS: Tuple[str, ...] = (
    "name", "cuisine", "category", "description", "ingredients",
    "serving_size_g", "serving_description",
    "calories", "protein_g", "carbs_g", "fat_g", "fiber_g", "sugar_g",
    "glycemic_index", "sodium_mg", "saturated_fat_g",
    "vitamin_a_mcg", "vitamin_c_mg", "vitamin_d_mcg", "vitamin_k_mcg", "b_vitamin_mg",
    "calcium_mg", "iron_mg", "potassium_mg",
    "diet_tags", "allergens",
)

#: Maximum page size accepted by the list endpoint.
MAX_PAGE_SIZE = 100
DEFAULT_PAGE_SIZE = 20


def _require_connection():
    """Return a live Oracle connection or raise 503 (distinct from 401/403)."""
    connection = get_connection()
    if connection is None:
        raise HTTPException(status_code=503, detail="Database connection failed")
    return connection


def _to_text(value: Any) -> Optional[str]:
    """Read a VARCHAR2/CLOB column as text.

    Oracle returns a CLOB as a LOB object whose content is only reachable via
    ``read()``; ``str()`` is tried as a fallback.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value
    read = getattr(value, "read", None)
    if callable(read):
        try:
            return str(read())
        except Exception:  # pragma: no cover - defensive, LOB read failure
            return None
    return str(value)


def _escape_like(value: str) -> str:
    """Escape the LIKE wildcards in a user-supplied value.

    Oracle's default LIKE escape character is the backslash, so ``%`` and
    ``_`` are neutralised. Without this a search for ``100%`` would match
    every row. This is not SQL injection protection -- the value still
    travels as a bind variable -- it only stops the wildcard from widening
    the match.
    """
    return (
        value.replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )


def _build_where(
    search: Optional[str],
    cuisine: Optional[str],
    category: Optional[str],
    diet_tag: Optional[str],
    max_glycemic_index: Optional[float],
    max_sodium_mg: Optional[float],
    min_protein_g: Optional[float],
    max_calories: Optional[float],
) -> Tuple[str, Dict[str, Any]]:
    """Build the WHERE clause and bind parameters for the list endpoint.

    Every filter is optional and they are combined with AND. The SQL is built
    from a fixed set of literal fragments -- every user-supplied value travels
    as a bind variable, so no value can be injected into the statement.
    """
    clauses: List[str] = []
    params: Dict[str, Any] = {}

    if search:
        # Case-insensitive "contains" on the dish name, cuisine or category.
        clauses.append(
            "(UPPER(name) LIKE :search ESCAPE '\\' "
            "OR UPPER(cuisine) LIKE :search ESCAPE '\\' "
            "OR UPPER(category) LIKE :search ESCAPE '\\')"
        )
        params["search"] = f"%{_escape_like(search.strip().upper())}%"

    if cuisine:
        clauses.append("UPPER(cuisine) = :cuisine")
        params["cuisine"] = cuisine.strip().upper()

    if category:
        clauses.append("UPPER(category) = :category")
        params["category"] = category.strip().upper()

    if diet_tag:
        # Diet tags are a comma separated list, so the tag must match as a
        # WHOLE token ("VEG" must not match "VEGAN"). The stored list is
        # de-spaced first so "A, B" still matches the token "B", and the four
        # patterns cover the only positions a token can occupy: alone, first,
        # last or in the middle.
        tag = _escape_like(diet_tag.strip().upper())
        tag_expr = "TRANSLATE(UPPER(diet_tags), ' ', '')"
        clauses.append(
            f"({tag_expr} = :diet_tag "
            f"OR {tag_expr} LIKE :diet_tag_prefix ESCAPE '\\' "
            f"OR {tag_expr} LIKE :diet_tag_suffix ESCAPE '\\' "
            f"OR {tag_expr} LIKE :diet_tag_contains ESCAPE '\\')"
        )
        params["diet_tag"] = tag
        params["diet_tag_prefix"] = f"{tag},%"
        params["diet_tag_suffix"] = f"%,{tag}"
        params["diet_tag_contains"] = f"%,{tag},%"

    if max_glycemic_index is not None:
        clauses.append("glycemic_index <= :max_glycemic_index")
        params["max_glycemic_index"] = max_glycemic_index

    if max_sodium_mg is not None:
        clauses.append("sodium_mg <= :max_sodium_mg")
        params["max_sodium_mg"] = max_sodium_mg

    if min_protein_g is not None:
        clauses.append("protein_g >= :min_protein_g")
        params["min_protein_g"] = min_protein_g

    if max_calories is not None:
        clauses.append("calories <= :max_calories")
        params["max_calories"] = max_calories

    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    return where, params


def _filters_model(
    search: Optional[str],
    cuisine: Optional[str],
    category: Optional[str],
    diet_tag: Optional[str],
    max_glycemic_index: Optional[float],
    max_sodium_mg: Optional[float],
    min_protein_g: Optional[float],
    max_calories: Optional[float],
) -> DishDnaFilters:
    """Echo the active filters back to the client."""
    return DishDnaFilters(
        search=search,
        cuisine=cuisine,
        category=category,
        diet_tag=diet_tag,
        max_glycemic_index=max_glycemic_index,
        max_sodium_mg=max_sodium_mg,
        min_protein_g=min_protein_g,
        max_calories=max_calories,
    )


# --------------------------------------------------------------------------- #
# GET /api/dish-dna
# --------------------------------------------------------------------------- #
@router.get("", response_model=DishDnaListResponse)
def list_dishes(
    search: Optional[str] = Query(default=None, max_length=150),
    cuisine: Optional[str] = Query(default=None, max_length=100),
    category: Optional[str] = Query(default=None, max_length=80),
    diet_tag: Optional[str] = Query(default=None, max_length=80),
    max_glycemic_index: Optional[float] = Query(default=None, ge=0, le=150),
    max_sodium_mg: Optional[float] = Query(default=None, ge=0, le=100000),
    min_protein_g: Optional[float] = Query(default=None, ge=0, le=1000),
    max_calories: Optional[float] = Query(default=None, ge=0, le=10000),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    principal: Principal = Depends(require_authenticated),
):
    """List dishes with optional search and nutritional filters.

    Any authenticated principal (patient or doctor) may read the Dish DNA:
    it is shared reference data that patients need in order to make informed
    diet decisions. The response envelope carries ``total`` (records matching
    the filters), ``count`` (records on this page) and the active ``filters``.
    """
    where, params = _build_where(
        search, cuisine, category, diet_tag,
        max_glycemic_index, max_sodium_mg, min_protein_g, max_calories,
    )

    connection = _require_connection()
    cursor = connection.cursor()
    try:
        # Total number of records matching the filters (for pagination).
        cursor.execute(f"SELECT COUNT(*) FROM dish_dna{where}", **params)
        total_row = cursor.fetchone()
        total = int(total_row[0]) if total_row and total_row[0] is not None else 0

        # The requested page of records. OFFSET/FETCH is the Oracle 12c+ form.
        cursor.execute(
            f"SELECT {_COLS} FROM dish_dna{where} "
            "ORDER BY name, dish_id "
            "OFFSET :offset ROWS FETCH NEXT :page_size ROWS ONLY",
            offset=(page - 1) * page_size,
            page_size=page_size,
            **params,
        )
        rows = cursor.fetchall() or []

        dishes = [DishDnaResponse.build(row) for row in rows]
        return DishDnaListResponse(
            dishes=dishes,
            total=total,
            count=len(dishes),
            filters=_filters_model(
                search, cuisine, category, diet_tag,
                max_glycemic_index, max_sodium_mg, min_protein_g, max_calories,
            ),
            disclaimer=DECISION_SUPPORT_DISCLAIMER,
        )
    except HTTPException:
        raise
    except oracledb.Error as error:
        logger.error(
            "SCRUM-47 list failed: %s",
            getattr(error.args[0] if error.args else None, "code", None),
        )
        raise HTTPException(status_code=500, detail="Unable to retrieve the dish list.")
    finally:
        cursor.close()
        connection.close()


# --------------------------------------------------------------------------- #
# GET /api/dish-dna/facets
# --------------------------------------------------------------------------- #
@router.get("/facets")
def list_facets(principal: Principal = Depends(require_authenticated)):
    """Return the distinct cuisines, categories and diet tags in the catalogue.

    The UI filter bar uses this to populate its dropdowns, so it never has to
    hard-code a list that could drift away from the stored data.
    """
    connection = _require_connection()
    cursor = connection.cursor()
    try:
        cursor.execute(
            "SELECT DISTINCT cuisine FROM dish_dna "
            "WHERE cuisine IS NOT NULL ORDER BY cuisine"
        )
        cuisines = [_to_text(row[0]) for row in (cursor.fetchall() or [])]

        cursor.execute(
            "SELECT DISTINCT category FROM dish_dna "
            "WHERE category IS NOT NULL ORDER BY category"
        )
        categories = [_to_text(row[0]) for row in (cursor.fetchall() or [])]

        cursor.execute("SELECT diet_tags FROM dish_dna WHERE diet_tags IS NOT NULL")
        tag_set = set()
        for row in (cursor.fetchall() or []):
            for tag in _split_tags(_to_text(row[0])):
                tag_set.add(tag)

        return {
            "cuisines": [c for c in cuisines if c],
            "categories": [c for c in categories if c],
            "diet_tags": sorted(tag_set),
            "disclaimer": DECISION_SUPPORT_DISCLAIMER,
        }
    except HTTPException:
        raise
    except oracledb.Error as error:
        logger.error(
            "SCRUM-47 facets failed: %s",
            getattr(error.args[0] if error.args else None, "code", None),
        )
        raise HTTPException(status_code=500, detail="Unable to retrieve the filter options.")
    finally:
        cursor.close()
        connection.close()


# --------------------------------------------------------------------------- #
# GET /api/dish-dna/{dish_id}
# --------------------------------------------------------------------------- #
@router.get("/{dish_id}", response_model=DishDnaResponse)
def get_dish(
    dish_id: int,
    principal: Principal = Depends(require_authenticated),
):
    """Read one dish by id.

    A dish that does not exist is a 404, not an empty record.
    """
    connection = _require_connection()
    cursor = connection.cursor()
    try:
        cursor.execute(
            f"SELECT {_COLS} FROM dish_dna WHERE dish_id = :dish_id",
            dish_id=dish_id,
        )
        row = cursor.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Dish not found.")
        return DishDnaResponse.build(row)
    except HTTPException:
        raise
    except oracledb.Error as error:
        logger.error(
            "SCRUM-47 read failed: %s",
            getattr(error.args[0] if error.args else None, "code", None),
        )
        raise HTTPException(status_code=500, detail="Unable to retrieve the dish.")
    finally:
        cursor.close()
        connection.close()



# --------------------------------------------------------------------------- #
# POST /api/dish-dna
# --------------------------------------------------------------------------- #
@router.post("", response_model=DishDnaResponse, status_code=status.HTTP_201_CREATED)
def create_dish(
    payload: DishDnaCreate,
    principal: Principal = Depends(require_doctor),
):
    """Create a new Dish DNA record (DOCTOR only).

    IDENTITY-COLUMN INSERT: ``dish_id`` is ``GENERATED ALWAYS AS IDENTITY``, so
    the column is deliberately ABSENT from the column list and no sequence is
    referenced. The generated key comes back with
    ``RETURNING dish_id INTO :new_id``; ``cursor.var(int).getvalue()`` returns a
    one-element LIST, so the id is ``getvalue()[0]``.
    """
    connection = _require_connection()
    cursor = connection.cursor()
    try:
        values = payload.model_dump(exclude_unset=True)
        # ``name`` is required, but a client could still send an empty string.
        values["name"] = str(values.get("name", "")).strip()

        columns = [c for c in _WRITABLE_COLUMNS if c in values]
        placeholders = [f":{c}" for c in columns]
        params: Dict[str, Any] = {c: values[c] for c in columns}

        new_id = cursor.var(int)
        cursor.execute(
            f"INSERT INTO dish_dna ({', '.join(columns)}) "
            f"VALUES ({', '.join(placeholders)}) "
            "RETURNING dish_id INTO :new_id",
            new_id=new_id,
            **params,
        )
        connection.commit()

        generated = new_id.getvalue()
        dish_id = int(generated[0]) if generated else None
        if dish_id is None:
            raise HTTPException(status_code=500, detail="Unable to create the dish.")

        cursor.execute(
            f"SELECT {_COLS} FROM dish_dna WHERE dish_id = :dish_id",
            dish_id=dish_id,
        )
        row = cursor.fetchone()
        if row is None:
            raise HTTPException(status_code=500, detail="Unable to create the dish.")
        return DishDnaResponse.build(row)
    except HTTPException:
        connection.rollback()
        raise
    except oracledb.Error as error:
        connection.rollback()
        logger.error(
            "SCRUM-47 create failed: %s",
            getattr(error.args[0] if error.args else None, "code", None),
        )
        raise HTTPException(status_code=500, detail="Unable to create the dish.")
    finally:
        cursor.close()
        connection.close()




# --------------------------------------------------------------------------- #
# PUT /api/dish-dna/{dish_id}
# --------------------------------------------------------------------------- #
@router.put("/{dish_id}", response_model=DishDnaResponse)
def update_dish(
    dish_id: int,
    payload: DishDnaUpdate,
    principal: Principal = Depends(require_doctor),
):
    """Update a Dish DNA record (DOCTOR only).

    This is a PARTIAL update: only the fields the client actually sent are
    written (``exclude_unset``). Omitted fields keep their stored value
    instead of being overwritten with NULL.
    """
    connection = _require_connection()
    cursor = connection.cursor()
    try:
        values = payload.model_dump(exclude_unset=True)
        if not values:
            raise HTTPException(
                status_code=400,
                detail="No updatable fields were supplied.",
            )

        if "name" in values:
            values["name"] = str(values["name"]).strip()

        columns = [c for c in _WRITABLE_COLUMNS if c in values]
        if not columns:
            raise HTTPException(
                status_code=400,
                detail="No updatable fields were supplied.",
            )

        assignments = ", ".join(f"{c} = :{c}" for c in columns)
        params: Dict[str, Any] = {c: values[c] for c in columns}

        cursor.execute(
            f"UPDATE dish_dna SET {assignments}, updated_at = SYSTIMESTAMP "
            "WHERE dish_id = :dish_id",
            dish_id=dish_id,
            **params,
        )

        if cursor.rowcount == 0:
            connection.rollback()
            raise HTTPException(status_code=404, detail="Dish not found.")

        connection.commit()

        cursor.execute(
            f"SELECT {_COLS} FROM dish_dna WHERE dish_id = :dish_id",
            dish_id=dish_id,
        )
        row = cursor.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Dish not found.")
        return DishDnaResponse.build(row)
    except HTTPException:
        connection.rollback()
        raise
    except oracledb.Error as error:
        connection.rollback()
        logger.error(
            "SCRUM-47 update failed: %s",
            getattr(error.args[0] if error.args else None, "code", None),
        )
        raise HTTPException(status_code=500, detail="Unable to update the dish.")
    finally:
        cursor.close()
        connection.close()


# --------------------------------------------------------------------------- #
# DELETE /api/dish-dna/{dish_id}
# --------------------------------------------------------------------------- #
@router.delete("/{dish_id}")
def delete_dish(
    dish_id: int,
    principal: Principal = Depends(require_doctor),
):
    """Delete a Dish DNA record (DOCTOR only).

    Returns ``{"deleted": True, "dish_id": <id>}`` so the client can remove
    the row from its list without a second round trip.
    """
    connection = _require_connection()
    cursor = connection.cursor()
    try:
        cursor.execute("DELETE FROM dish_dna WHERE dish_id = :dish_id", dish_id=dish_id)
        if cursor.rowcount == 0:
            connection.rollback()
            raise HTTPException(status_code=404, detail="Dish not found.")
        connection.commit()
        return {"deleted": True, "dish_id": dish_id}
    except HTTPException:
        connection.rollback()
        raise
    except oracledb.Error as error:
        connection.rollback()
        logger.error(
            "SCRUM-47 delete failed: %s",
            getattr(error.args[0] if error.args else None, "code", None),
        )
        raise HTTPException(status_code=500, detail="Unable to delete the dish.")
    finally:
        cursor.close()
        connection.close()
