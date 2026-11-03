"""SCRUM-47 tests: Dish DNA backend (nutritional fingerprint of a dish).

Runs WITHOUT a live Oracle connection by driving the real router functions and
the real SCRUM-44 security layer against a small in-memory fake DB that models
Oracle's identity-column semantics.

What is proven here
-------------------
1. IDENTITY-COLUMN INSERT: ``DISH_ID`` is ``GENERATED ALWAYS AS IDENTITY``, so
   the router's INSERT must NOT name the column and must NOT reference a
   sequence. The generated key is fetched with ``RETURNING dish_id INTO
   :new_id``; ``cursor.var(int).getvalue()`` returns a one-element LIST, so the
   id is ``getvalue()[0]``. Any SQL that mentions ``dish_id`` in the column
   list or ``dish_dna_seq`` is a regression.
2. RBAC: reads are open to any authenticated principal (patients need the
   reference data); writes are DOCTOR-only, so a PATIENT gets 403.
3. Partial update: only the fields the client actually sent are written --
   an omitted field keeps its stored value and is never blanked with NULL.
4. Derived health badges: the badges are computed server-side from the
   stored nutrients, and an UNKNOWN nutrient (None) never produces a badge.
5. The decision-support disclaimer is present on every response.
6. Filters are parameterised: a user-supplied value always travels as a bind
   variable and never as SQL text.

No passwords, hashes, tokens or other secrets are ever printed.
"""

import asyncio
import os
import sys
from datetime import datetime, timezone
from decimal import Decimal
from inspect import signature
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-scrum47")

import pytest
from fastapi import HTTPException

from backend.core.security import ROLE_DOCTOR, ROLE_PATIENT, Principal, require_doctor, require_doctor_or_admin
import backend.routers.dish_dna_router as dna
from backend.schemas.dish_dna import (
    DECISION_SUPPORT_DISCLAIMER,
    STATUS_CALORIE_LIGHT,
    STATUS_DIABETIC_FRIENDLY,
    STATUS_HIGH_FIBER,
    STATUS_HIGH_PROTEIN,
    STATUS_LOW_SODIUM,
    DishDnaCreate,
    DishDnaListResponse,
    DishDnaResponse,
    DishDnaUpdate,
    derive_health_statuses,
)


def run(func_result):
    """Drive async dependency/endpoint calls on a fresh event loop if needed."""
    if asyncio.iscoroutine(func_result):
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(func_result)
        finally:
            loop.close()
    return func_result


def status_of(excinfo):
    assert excinfo.value.status_code in (400, 401, 403, 404, 500, 503), (
        "expected an HTTP error"
    )
    return excinfo.value.status_code



# --------------------------------------------------------------------------- #
# In-memory fake Oracle that models GENERATED ALWAYS AS IDENTITY
# --------------------------------------------------------------------------- #

SENTINEL_TS = datetime(2024, 1, 1, 12, 0, 0)

#: Column order of a DISH_DNA row -- MUST match the router's ``_COLS``.
_ROW_COLUMNS = (
    "dish_id", "name", "cuisine", "category", "description", "ingredients",
    "serving_size_g", "serving_description",
    "calories", "protein_g", "carbs_g", "fat_g", "fiber_g", "sugar_g",
    "glycemic_index", "sodium_mg", "saturated_fat_g",
    "vitamin_a_mcg", "vitamin_c_mg", "vitamin_d_mcg", "vitamin_k_mcg", "b_vitamin_mg",
    "calcium_mg", "iron_mg", "potassium_mg",
    "diet_tags", "allergens", "created_at", "updated_at",
)


def make_row(dish_id, **overrides):
    """Build a complete DISH_DNA row (as a dict) with sensible defaults."""
    row = {
        "dish_id": dish_id,
        "name": f"Dish {dish_id}",
        "cuisine": "Italian",
        "category": "Main",
        "description": "A tasty dish.",
        "ingredients": "tomato, basil",
        "serving_size_g": 250.0,
        "serving_description": "1 bowl",
        "calories": 320.0,
        "protein_g": 12.0,
        "carbs_g": 40.0,
        "fat_g": 9.0,
        "fiber_g": 2.0,
        "sugar_g": 5.0,
        "glycemic_index": 62.0,
        "sodium_mg": 480.0,
        "saturated_fat_g": 2.0,
        "vitamin_a_mcg": 50.0,
        "vitamin_c_mg": 8.0,
        "vitamin_d_mcg": 0.0,
        "vitamin_k_mcg": 12.0,
        "b_vitamin_mg": 0.3,
        "calcium_mg": 60.0,
        "iron_mg": 1.5,
        "potassium_mg": 420.0,
        "diet_tags": "Vegetarian",
        "allergens": "Gluten",
        "created_at": SENTINEL_TS,
        "updated_at": SENTINEL_TS,
    }
    row.update(overrides)
    return row


class FakeVar:
    """Stand-in for ``cursor.var(int)``: ``getvalue()`` returns a LIST."""

    def __init__(self):
        self._values = []

    def getvalue(self):
        return list(self._values)

    def setvalue(self, value):
        self._values = list(value)



class FakeCursor:
    """Records statements and mimics identity-column DML.

    ``cursor.var(int)`` returns a holder whose ``getvalue()`` yields a
    one-element LIST, exactly like python-oracledb does for a DML
    ``RETURNING INTO`` bind.
    """

    def __init__(self, db):
        self._db = db
        self._row = None
        self._rows = []
        self.closed = False
        self.rowcount = 0

    def var(self, type_code=None):
        return FakeVar()

    # -- helpers ---------------------------------------------------------- #
    def _match(self, row, params):
        """Apply the router's WHERE clauses to one row (test-side mirror)."""
        if "search" in params:
            needle = params["search"].replace("\\", "").strip("%")
            hay = " ".join(
                str(row.get(c) or "") for c in ("name", "cuisine", "category")
            ).upper()
            if needle not in hay:
                return False
        if "cuisine" in params:
            if str(row.get("cuisine") or "").upper() != params["cuisine"]:
                return False
        if "category" in params:
            if str(row.get("category") or "").upper() != params["category"]:
                return False
        if "diet_tag" in params:
            tag = params["diet_tag"]
            tags = [
                t.strip().upper()
                for t in str(row.get("diet_tags") or "").split(",")
                if t.strip()
            ]
            if tag not in tags:
                return False
        for column, param in (
            ("glycemic_index", "max_glycemic_index"),
            ("sodium_mg", "max_sodium_mg"),
            ("protein_g", "min_protein_g"),
            ("calories", "max_calories"),
        ):
            if param in params:
                value = row.get(column)
                if value is None:
                    return False
                if param.startswith("max_") and value > params[param]:
                    return False
                if param.startswith("min_") and value < params[param]:
                    return False
        return True

    # -- the fake DB ------------------------------------------------------ #
    def execute(self, sql, **params):
        self._db.statements.append((" ".join(sql.split()), dict(params)))
        norm = " ".join(sql.split()).lower()

        if norm.startswith("insert into dish_dna"):
            self._db.insert_rows.append(dict(params))
            # Oracle allocates the identity value; the router never supplies it.
            new_id = self._db._next_dish_id()
            row = {c: None for c in _ROW_COLUMNS}
            row["dish_id"] = new_id
            for column in dna._WRITABLE_COLUMNS:
                if column in params:
                    row[column] = params[column]
            row["created_at"] = SENTINEL_TS
            row["updated_at"] = SENTINEL_TS
            self._db.dishes[new_id] = row
            self.rowcount = 1
            # Populate the RETURNING INTO bind variable.
            holder = params.get("new_id")
            if holder is not None:
                holder.setvalue([new_id])
            self._row = None

        elif norm.startswith("update dish_dna set"):
            dish_id = params.get("dish_id")
            row = self._db.dishes.get(dish_id)
            if row is None:
                self.rowcount = 0
                self._row = None
            else:
                for column in dna._WRITABLE_COLUMNS:
                    if column in params:
                        row[column] = params[column]
                row["updated_at"] = SENTINEL_TS
                self.rowcount = 1
                self._row = None

        elif norm.startswith("delete from dish_dna"):
            dish_id = params.get("dish_id")
            if dish_id in self._db.dishes:
                del self._db.dishes[dish_id]
                self.rowcount = 1
            else:
                self.rowcount = 0
            self._row = None

        elif norm.startswith("select count(*)"):
            matches = [
                r for r in self._db.dishes.values() if self._match(r, params)
            ]
            self._row = (len(matches),)
            self._rows = []

        elif norm.startswith("select") and "from dish_dna" in norm:
            if "where dish_id = :dish_id" in norm:
                row = self._db.dishes.get(params.get("dish_id"))
                self._row = self._db._as_tuple(row) if row is not None else None
            elif "distinct cuisine" in norm:
                self._rows = sorted(
                    {(r["cuisine"],) for r in self._db.dishes.values()
                     if r.get("cuisine")}
                )
                self._row = None
            elif "distinct category" in norm:
                self._rows = sorted(
                    {(r["category"],) for r in self._db.dishes.values()
                     if r.get("category")}
                )
                self._row = None
            elif "select diet_tags" in norm:
                self._rows = [
                    (r.get("diet_tags"),)
                    for r in self._db.dishes.values()
                    if r.get("diet_tags")
                ]
                self._row = None
            else:
                matches = [
                    r for r in self._db.dishes.values() if self._match(r, params)
                ]
                matches.sort(key=lambda r: (str(r.get("name") or ""), r["dish_id"]))
                offset = params.get("offset", 0)
                size = params.get("page_size", len(matches))
                self._rows = [
                    self._db._as_tuple(r) for r in matches[offset:offset + size]
                ]
                self._row = None

    def fetchone(self):
        return self._row

    def fetchall(self):
        return list(self._rows)

    def close(self):
        self.closed = True


class FakeConnection:
    def __init__(self, dishes=None):
        self.dishes = dishes or {}
        self.statements = []
        self.insert_rows = []
        self.committed = False
        self.rolled_back = False
        self.closed = False
        self._dish_id_seq = 900

    def _next_dish_id(self):
        self._dish_id_seq += 1
        return self._dish_id_seq

    def _as_tuple(self, row):
        """Return the row in the router's _COLS order."""
        if row is None:
            return None
        return tuple(row.get(column) for column in _ROW_COLUMNS)

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


@pytest.fixture
def db(monkeypatch):
    """A fake DB pre-loaded with two dishes, wired into the router."""
    fake = FakeConnection(
        {
            1: make_row(1, name="Grilled Salmon", cuisine="Japanese",
                        category="Main", protein_g=30.0, sodium_mg=120.0,
                        glycemic_index=45.0, fiber_g=4.0, calories=140.0,
                        diet_tags="Gluten Free, High Protein"),
            2: make_row(2, name="Pasta Carbonara", cuisine="Italian",
                        category="Main", protein_g=18.0, sodium_mg=640.0,
                        glycemic_index=72.0, fiber_g=2.0, calories=560.0,
                        diet_tags="Vegetarian"),
        }
    )
    monkeypatch.setattr(dna, "get_connection", lambda: fake)
    return fake


def doctor_principal(subject_id=7):
    return Principal(subject=str(subject_id), role=ROLE_DOCTOR)


def patient_principal(subject_id=1):
    return Principal(subject=str(subject_id), role=ROLE_PATIENT)


def _list_call(**kwargs):
    """Call the list endpoint with sensible defaults."""
    params = {
        "search": None,
        "cuisine": None,
        "category": None,
        "diet_tag": None,
        "max_glycemic_index": None,
        "max_sodium_mg": None,
        "min_protein_g": None,
        "max_calories": None,
        "page": 1,
        "page_size": 20,
        "principal": patient_principal(1),
    }
    params.update(kwargs)
    return dna.list_dishes(**params)


def _insert_sql(db):
    return next(
        sql for sql, _ in db.statements
        if sql.lower().startswith("insert into dish_dna")
    )


def _update_sql(db):
    return next(
        sql for sql, _ in db.statements
        if sql.lower().startswith("update dish_dna set")
    )


# --------------------------------------------------------------------------- #
# 1) IDENTITY-COLUMN INSERT (the SCRUM-47 Oracle defect)
# --------------------------------------------------------------------------- #

def _create_call(db, body=None):
    payload = DishDnaCreate(**(body or {"name": "Miso Soup"}))
    return dna.create_dish(payload=payload, principal=doctor_principal(7))


def test_create_insert_does_not_name_the_identity_column(db):
    """The INSERT must not list DISH_ID: Oracle generates it (ORA-32795)."""
    _create_call(db)
    lowered = _insert_sql(db).lower()
    column_list = lowered[lowered.index("(") + 1:lowered.index(")")]
    assert "dish_id" not in column_list, (
        "DISH_ID is GENERATED ALWAYS AS IDENTITY; naming it in the INSERT "
        "raises ORA-32795"
    )


def test_create_insert_does_not_reference_a_sequence(db):
    """No sequence may be referenced: DISH_DNA_SEQ does not exist (ORA-02289)."""
    _create_call(db)
    sql = _insert_sql(db).lower()
    assert "dish_dna_seq" not in sql
    assert "nextval" not in sql


def test_create_uses_returning_into_for_the_generated_id(db):
    _create_call(db)
    assert "returning dish_id into :new_id" in _insert_sql(db).lower()


def test_create_returns_the_generated_dish_id(db):
    """getvalue() is a LIST, so the id is getvalue()[0] -- and it is the DB's."""
    response = _create_call(db)
    assert isinstance(response, DishDnaResponse)
    assert response.dish_id == 901
    assert response.dish_id in db.dishes


def test_create_commits_and_closes(db):
    _create_call(db)
    assert db.committed is True
    assert db.rolled_back is False
    assert db.closed is True


def test_create_rolls_back_and_raises_500_on_db_error(db, monkeypatch):
    """A failed insert must roll back and surface a 500, not leak an ORA error."""
    def boom(self, sql, **params):
        raise dna.oracledb.DatabaseError("ORA-02289: sequence does not exist")

    monkeypatch.setattr(FakeCursor, "execute", boom)
    with pytest.raises(HTTPException) as excinfo:
        _create_call(db)
    assert status_of(excinfo) == 500
    assert db.rolled_back is True
    assert db.committed is False
    assert db.closed is True


def test_create_stores_the_supplied_nutrients(db):
    _create_call(db, {
        "name": "Miso Soup",
        "cuisine": "Japanese",
        "calories": 90.5,
        "protein_g": 6.25,
        "sodium_mg": 890.0,
        "diet_tags": "Vegan, Low Fat",
    })
    row = db.dishes[901]
    assert row["name"] == "Miso Soup"
    assert row["calories"] == 90.5
    assert row["protein_g"] == 6.25
    assert row["diet_tags"] == "Vegan, Low Fat"


# --------------------------------------------------------------------------- #
# 2) RBAC -- reads are open, writes are DOCTOR-only
# --------------------------------------------------------------------------- #

def test_doctor_passes_require_doctor():
    assert run(require_doctor(principal=doctor_principal(7))) is not None


def test_patient_is_403_on_require_doctor():
    with pytest.raises(HTTPException) as excinfo:
        run(require_doctor(principal=patient_principal(1)))
    assert status_of(excinfo) == 403


def test_create_endpoint_is_bound_to_require_doctor():
    """Proves the create route cannot be reached without the DOCTOR dependency."""
    params = signature(dna.create_dish).parameters.values()
    principal = next(p for p in params if p.name == "principal")
    assert principal.default.dependency is require_doctor_or_admin  # SCRUM-61: admin may maintain Dish DNA


def test_update_endpoint_is_bound_to_require_doctor():
    params = signature(dna.update_dish).parameters.values()
    principal = next(p for p in params if p.name == "principal")
    assert principal.default.dependency is require_doctor_or_admin  # SCRUM-61: admin may maintain Dish DNA


def test_delete_endpoint_is_bound_to_require_doctor():
    params = signature(dna.delete_dish).parameters.values()
    principal = next(p for p in params if p.name == "principal")
    assert principal.default.dependency is require_doctor


def test_read_endpoints_are_open_to_a_patient(db):
    """Patients need the reference data to make diet decisions."""
    response = dna.get_dish(dish_id=1, principal=patient_principal(1))
    assert response.dish_id == 1


# --------------------------------------------------------------------------- #
# 3) Read + list
# --------------------------------------------------------------------------- #

def test_get_dish_returns_the_record(db):
    response = dna.get_dish(dish_id=2, principal=patient_principal(1))
    assert response.name == "Pasta Carbonara"
    assert response.cuisine == "Italian"


def test_get_dish_is_404_for_an_unknown_id(db):
    with pytest.raises(HTTPException) as excinfo:
        dna.get_dish(dish_id=4242, principal=patient_principal(1))
    assert status_of(excinfo) == 404


def test_get_dish_closes_the_connection(db):
    dna.get_dish(dish_id=1, principal=patient_principal(1))
    assert db.closed is True


def test_list_returns_every_dish_with_a_total(db):
    response = _list_call()
    assert isinstance(response, DishDnaListResponse)
    assert response.total == 2
    assert response.count == 2
    assert [d.name for d in response.dishes] == ["Grilled Salmon", "Pasta Carbonara"]


def test_list_carries_the_disclaimer(db):
    assert _list_call().disclaimer == DECISION_SUPPORT_DISCLAIMER


def test_single_dish_carries_the_disclaimer(db):
    assert dna.get_dish(dish_id=1, principal=patient_principal(1)).disclaimer == \
        DECISION_SUPPORT_DISCLAIMER


def test_list_echoes_the_active_filters(db):
    response = _list_call(search="salmon", cuisine="Japanese")
    assert response.filters.search == "salmon"
    assert response.filters.cuisine == "Japanese"


def test_list_paginates(db):
    page1 = _list_call(page=1, page_size=1)
    page2 = _list_call(page=2, page_size=1)
    assert page1.count == 1
    assert page2.count == 1
    assert page1.dishes[0].name != page2.dishes[0].name
    # The total still reflects every matching record, not just the page.
    assert page1.total == 2
    assert page2.total == 2


def test_search_filters_by_name(db):
    response = _list_call(search="salmon")
    assert response.total == 1
    assert response.dishes[0].name == "Grilled Salmon"


def test_search_is_case_insensitive(db):
    assert _list_call(search="SALMON").total == 1


def test_search_matches_cuisine_and_category(db):
    assert _list_call(search="italian").total == 1
    assert _list_call(search="main").total == 2


def test_cuisine_filter(db):
    response = _list_call(cuisine="Italian")
    assert response.total == 1
    assert response.dishes[0].name == "Pasta Carbonara"


def test_diet_tag_filter_matches_a_whole_token(db):
    """'Gluten Free' must match, and it must not be confused with 'Gluten'."""
    assert _list_call(diet_tag="Gluten Free").total == 1
    assert _list_call(diet_tag="Vegetarian").total == 1
    assert _list_call(diet_tag="Gluten").total == 0


def test_nutritional_filters_are_applied(db):
    assert _list_call(max_sodium_mg=200).total == 1
    assert _list_call(min_protein_g=25).total == 1
    assert _list_call(max_glycemic_index=50).total == 1
    assert _list_call(max_calories=200).total == 1


def test_filters_are_combined_with_and(db):
    """Two filters must both hold, not either of them."""
    assert _list_call(cuisine="Italian", max_sodium_mg=200).total == 0


def test_user_values_are_bound_not_interpolated(db):
    """A hostile search term must travel as a bind value, never as SQL text."""
    _list_call(search="'; DROP TABLE dish_dna; --")
    statement = db.statements[-1][0]
    assert "drop table" not in statement.lower()
    assert "DROP TABLE dish_dna" not in db.statements[-1][1].get("search", "")


# --------------------------------------------------------------------------- #
# 4) Partial update -- omitted fields must keep their stored value
# --------------------------------------------------------------------------- #

def _update_call(db, dish_id=1, **body):
    payload = DishDnaUpdate(**body)
    return dna.update_dish(dish_id=dish_id, payload=payload, principal=doctor_principal(7))


def test_update_only_writes_the_supplied_fields(db):
    _update_call(db, calories=999.0)
    row = db.dishes[1]
    assert row["calories"] == 999.0
    # Everything else must be untouched -- NOT blanked with NULL.
    assert row["name"] == "Grilled Salmon"
    assert row["protein_g"] == 30.0
    assert row["sodium_mg"] == 120.0
    assert row["diet_tags"] == "Gluten Free, High Protein"


def test_update_does_not_set_the_omitted_columns_to_null(db):
    """A NULL-check regression: the SET clause must only name supplied columns."""
    _update_call(db, calories=111.0)
    sql = _update_sql(db).lower()
    assignments = sql[sql.index("set ") + 4:sql.index(" where ")]
    assert "protein_g" not in assignments
    assert "sodium_mg" not in assignments


def test_update_returns_the_stored_row(db):
    response = _update_call(db, name="Grilled Salmon Fillet")
    assert response.name == "Grilled Salmon Fillet"
    assert response.dish_id == 1


def test_update_commits_and_closes(db):
    _update_call(db, calories=123.0)
    assert db.committed is True
    assert db.rolled_back is False
    assert db.closed is True


def test_update_is_404_for_an_unknown_dish(db):
    with pytest.raises(HTTPException) as excinfo:
        _update_call(db, dish_id=4242, calories=100.0)
    assert status_of(excinfo) == 404
    assert db.rolled_back is True


def test_update_with_no_fields_is_400(db):
    with pytest.raises(HTTPException) as excinfo:
        _update_call(db)
    assert status_of(excinfo) == 400


def test_update_rolls_back_and_raises_500_on_db_error(db, monkeypatch):
    def boom(self, sql, **params):
        raise dna.oracledb.DatabaseError("ORA-00942: table or view does not exist")

    monkeypatch.setattr(FakeCursor, "execute", boom)
    with pytest.raises(HTTPException) as excinfo:
        _update_call(db, calories=100.0)
    assert status_of(excinfo) == 500
    assert db.rolled_back is True
    assert db.committed is False


# --------------------------------------------------------------------------- #
# 5) Delete
# --------------------------------------------------------------------------- #

def test_delete_removes_the_dish(db):
    result = dna.delete_dish(dish_id=1, principal=doctor_principal(7))
    assert result == {"deleted": True, "dish_id": 1}
    assert 1 not in db.dishes
    assert db.committed is True


# --------------------------------------------------------------------------- #
# 6) Derived health badges
# --------------------------------------------------------------------------- #

def test_derive_badges_high_protein():
    assert STATUS_HIGH_PROTEIN in derive_health_statuses(protein_g=25.0)


def test_derive_badges_low_sodium():
    assert STATUS_LOW_SODIUM in derive_health_statuses(sodium_mg=100.0)


def test_derive_badges_diabetic_friendly():
    assert STATUS_DIABETIC_FRIENDLY in derive_health_statuses(glycemic_index=40.0)


def test_derive_badges_high_fiber():
    assert STATUS_HIGH_FIBER in derive_health_statuses(fiber_g=5.0)


def test_derive_badges_calorie_light():
    assert STATUS_CALORIE_LIGHT in derive_health_statuses(calories=120.0)


def test_derive_badges_are_empty_for_an_ordinary_dish():
    assert derive_health_statuses(
        protein_g=5.0, sodium_mg=900.0, glycemic_index=80.0, calories=600.0
    ) == []


def test_unknown_nutrient_never_produces_a_badge():
    """An unknown value is None, NOT zero -- so it must not qualify."""
    assert derive_health_statuses(sodium_mg=None, calories=None) == []
    # sodium_mg=None must not be treated as 0 (which would be "low sodium").
    assert STATUS_LOW_SODIUM not in derive_health_statuses(sodium_mg=None)
    assert STATUS_CALORIE_LIGHT not in derive_health_statuses(calories=None)


def test_badges_are_derived_server_side_on_read(db):
    """The stored dish (120 mg sodium, 140 kcal) gets its badges from the API."""
    response = dna.get_dish(dish_id=1, principal=patient_principal(1))
    assert STATUS_LOW_SODIUM in response.health_statuses
    assert STATUS_CALORIE_LIGHT in response.health_statuses
    assert STATUS_HIGH_PROTEIN in response.health_statuses
    assert "Low Sodium" in response.health_status_labels


def test_a_dish_without_badges_has_an_empty_label_list(db):
    response = dna.get_dish(dish_id=2, principal=patient_principal(1))
    assert response.health_statuses == []
    assert response.health_status_labels == []


def test_badge_derivation_is_consistent_between_read_paths(db):
    one = dna.get_dish(dish_id=1, principal=patient_principal(1))
    listed = _list_call(search="salmon").dishes[0]
    assert one.health_statuses == listed.health_statuses
    assert one.health_status_labels == listed.health_status_labels


# --------------------------------------------------------------------------- #
# 7) Schema / row conversion
# --------------------------------------------------------------------------- #

def test_build_converts_oracle_decimals_to_floats(db):
    """Oracle returns NUMBER as Decimal; the API must send plain numbers."""
    row = db._as_tuple(db.dishes[1])
    decimal_row = tuple(
        Decimal(str(value)) if isinstance(value, float) else value
        for value in row
    )
    response = DishDnaResponse.build(decimal_row)
    assert isinstance(response.calories, float)
    assert isinstance(response.protein_g, float)
    assert response.calories == 140.0


def test_build_keeps_an_unknown_nutrient_as_none(db):
    row = db._as_tuple(db.dishes[1])
    nulled = list(row)
    nulled[9] = None  # protein_g
    response = DishDnaResponse.build(tuple(nulled))
    assert response.protein_g is None
    assert STATUS_HIGH_PROTEIN not in response.health_statuses


def test_build_splits_the_tag_lists(db):
    response = DishDnaResponse.build(db._as_tuple(db.dishes[1]))
    assert response.diet_tags == ["Gluten Free", "High Protein"]
    assert response.allergens == ["Gluten"]


def test_build_reads_a_clob_through_str(db):
    """A CLOB arrives as a LOB object; its text must survive."""
    class FakeClob:
        def __init__(self, text):
            self._text = text

        def read(self):
            return self._text

    row = list(db._as_tuple(db.dishes[1]))
    row[5] = FakeClob("salmon, lemon, dill")
    response = DishDnaResponse.build(tuple(row))
    assert response.ingredients == "salmon, lemon, dill"


def test_dish_id_is_never_a_writable_field():
    """DISH_ID is GENERATED ALWAYS AS IDENTITY -- it is not in the body model."""
    assert "dish_id" not in DishDnaCreate.model_fields
    assert "dish_id" not in DishDnaUpdate.model_fields


def test_create_model_requires_a_name():
    with pytest.raises(Exception):
        DishDnaCreate()


def test_create_model_rejects_a_negative_nutrient():
    with pytest.raises(Exception):
        DishDnaCreate(name="X", calories=-5)


def test_update_model_accepts_an_empty_body():
    """An empty update is a 400 at the router, not a validation error here."""
    assert DishDnaUpdate().model_dump(exclude_unset=True) == {}



def test_delete_is_404_for_an_unknown_dish(db):
    with pytest.raises(HTTPException) as excinfo:
        dna.delete_dish(dish_id=4242, principal=doctor_principal(7))
    assert status_of(excinfo) == 404
    assert db.rolled_back is True


def test_delete_rolls_back_and_raises_500_on_db_error(db, monkeypatch):
    def boom(self, sql, **params):
        raise dna.oracledb.DatabaseError("ORA-00942: table or view does not exist")

    monkeypatch.setattr(FakeCursor, "execute", boom)
    with pytest.raises(HTTPException) as excinfo:
        dna.delete_dish(dish_id=1, principal=doctor_principal(7))
    assert status_of(excinfo) == 500
    assert db.rolled_back is True


def test_facets_returns_the_distinct_values(db):
    facets = dna.list_facets(principal=patient_principal(1))
    assert facets["cuisines"] == ["Italian", "Japanese"]
    assert facets["categories"] == ["Main"]
    assert facets["diet_tags"] == ["Gluten Free", "High Protein", "Vegetarian"]
    assert facets["disclaimer"] == DECISION_SUPPORT_DISCLAIMER


def test_create_returns_201_status_code():
    """The create route must answer 201, not the default 200."""
    route = next(
        r for r in dna.router.routes
        if getattr(r, "endpoint", None) is dna.create_dish
    )
    assert route.status_code == 201
