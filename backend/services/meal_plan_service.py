"""Personalized meal plan service (SCRUM-49, SRS 3.1.3.8).

Builds a deterministic, prescription-aware meal plan from the patient's
profile, active prescriptions, conditions, preferences and budget, and
persists it so the current plan can be read back and regenerated.

The generator enforces every active dietary restriction returned by the
SCRUM-48 conflict resolver. When restrictions contradict each other and
no safe intersection exists, the plan is flagged `requires_review`
instead of being guessed.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from backend.database.connection import SessionLocal
from backend.models.diet_plan import DietPlan
from backend.models.prescription import Prescription
from backend.models.user import User
from backend.schemas.meal_plan import DECISION_SUPPORT_DISCLAIMER
from backend.services.conflict_resolver import (
    SEVERITY_HIGH,
    extract_prescription_restrictions,
    find_dietary_conflicts,
)
from backend.services.diet_generator import SAFE_MIN_CALORIES, split_macros

STATUS_CURRENT = "CURRENT"
STATUS_SUPERSEDED = "SUPERSEDED"

DEFAULT_CALORIE_TARGET = 2000.0
DEFAULT_SCHEDULE = "bengali"

#: Meal schedules. Bengali times reflect the local eating pattern (light
#: breakfast, main rice-and-fish lunch, afternoon snack, early dinner).
MEAL_SCHEDULES: Dict[str, List[Dict[str, Any]]] = {
    "bengali": [
        {"time": "07:30", "label": "Breakfast", "bangla_label": "\u0938\u093e\u0932\u0917\u094d\u0930\u0930\u0947\u0928\u093e \u0928\u093e\u0938\u094d\u091f\u093e", "meal_type": "breakfast", "share": 0.25},
        {"time": "13:30", "label": "Lunch", "bangla_label": "\u0926\u0941\u092a\u0942\u0930\u0947\u0928\u093e \u0916\u093e\u092c\u093e\u0930", "meal_type": "lunch", "share": 0.35},
        {"time": "16:30", "label": "Afternoon snack", "bangla_label": "\u0935\u0947\u0932\u093e\u091f\u093e \u0928\u093e\u0938\u094d\u091f\u093e", "meal_type": "snack", "share": 0.10},
        {"time": "20:30", "label": "Dinner", "bangla_label": "\u0930\u093e\u091f\u0947\u0928\u093e \u0916\u093e\u092c\u093e\u0930", "meal_type": "dinner", "share": 0.30},
    ],
    "standard": [
        {"time": "07:30", "label": "Breakfast", "bangla_label": None, "meal_type": "breakfast", "share": 0.25},
        {"time": "12:30", "label": "Lunch", "bangla_label": None, "meal_type": "lunch", "share": 0.35},
        {"time": "15:30", "label": "Afternoon snack", "bangla_label": None, "meal_type": "snack", "share": 0.10},
        {"time": "19:30", "label": "Dinner", "bangla_label": None, "meal_type": "dinner", "share": 0.30},
    ],
}

#: Deterministic food catalog (approximate values per 100 g serving). The
#: same catalog drives every plan so identical inputs always yield the
#: identical plan. Values are decision-support estimates only.
FOOD_CATALOG: List[Dict[str, Any]] = [
    {"name": "Steamed rice", "category": "grains", "calories": 130, "protein_g": 2.7, "carbs_g": 28.0, "fats_g": 0.3, "sugars_g": 0.1, "sodium_mg": 1, "potassium_mg": 35, "cost_bdt": 12.0},
    {"name": "Roti (whole wheat)", "category": "bakery", "calories": 100, "protein_g": 3.0, "carbs_g": 22.0, "fats_g": 3.0, "sugars_g": 1.0, "sodium_mg": 150, "potassium_mg": 100, "cost_bdt": 10.0},
    {"name": "Luchi (fried bread)", "category": "bakery", "calories": 150, "protein_g": 3.0, "carbs_g": 18.0, "fats_g": 7.0, "sugars_g": 1.0, "sodium_mg": 220, "potassium_mg": 60, "cost_bdt": 15.0},
    {"name": "Fish curry (rohu)", "category": "fish", "calories": 200, "protein_g": 18.0, "carbs_g": 6.0, "fats_g": 10.0, "sugars_g": 2.0, "sodium_mg": 350, "potassium_mg": 400, "cost_bdt": 60.0},
    {"name": "Hilsa curry", "category": "fish", "calories": 260, "protein_g": 20.0, "carbs_g": 4.0, "fats_g": 18.0, "sugars_g": 1.0, "sodium_mg": 400, "potassium_mg": 450, "cost_bdt": 120.0},
    {"name": "Chicken curry", "category": "poultry", "calories": 250, "protein_g": 25.0, "carbs_g": 4.0, "fats_g": 16.0, "sugars_g": 2.0, "sodium_mg": 450, "potassium_mg": 380, "cost_bdt": 70.0},
    {"name": "Egg curry", "category": "eggs", "calories": 160, "protein_g": 13.0, "carbs_g": 4.0, "fats_g": 11.0, "sugars_g": 2.0, "sodium_mg": 300, "potassium_mg": 140, "cost_bdt": 20.0},
    {"name": "Dal (lentil soup)", "category": "pulses", "calories": 115, "protein_g": 8.0, "carbs_g": 20.0, "fats_g": 0.5, "sugars_g": 2.0, "sodium_mg": 300, "potassium_mg": 300, "cost_bdt": 25.0},
    {"name": "Mixed vegetable curry", "category": "vegetables", "calories": 90, "protein_g": 2.5, "carbs_g": 16.0, "fats_g": 3.0, "sugars_g": 4.0, "sodium_mg": 200, "potassium_mg": 320, "cost_bdt": 20.0},
    {"name": "Alu bhorta (mashed potato)", "category": "vegetables", "calories": 160, "protein_g": 2.0, "carbs_g": 20.0, "fats_g": 8.0, "sugars_g": 3.0, "sodium_mg": 400, "potassium_mg": 500, "cost_bdt": 15.0},
    {"name": "Palong shak (spinach)", "category": "vegetables", "calories": 23, "protein_g": 2.9, "carbs_g": 3.6, "fats_g": 0.4, "sugars_g": 0.4, "sodium_mg": 79, "potassium_mg": 558, "cost_bdt": 15.0},
    {"name": "Panta bhat (fermented rice)", "category": "grains", "calories": 130, "protein_g": 2.0, "carbs_g": 28.0, "fats_g": 0.3, "sugars_g": 0.1, "sodium_mg": 400, "potassium_mg": 30, "cost_bdt": 12.0},
    {"name": "Chira (flattened rice)", "category": "grains", "calories": 120, "protein_g": 2.0, "carbs_g": 26.0, "fats_g": 0.5, "sugars_g": 0.2, "sodium_mg": 10, "potassium_mg": 60, "cost_bdt": 12.0},
    {"name": "Muri (puffed rice)", "category": "grains", "calories": 80, "protein_g": 1.5, "carbs_g": 18.0, "fats_g": 0.2, "sugars_g": 0.1, "sodium_mg": 5, "potassium_mg": 40, "cost_bdt": 10.0},
    {"name": "Banana", "category": "fruit", "calories": 105, "protein_g": 1.3, "carbs_g": 27.0, "fats_g": 0.3, "sugars_g": 14.0, "sodium_mg": 1, "potassium_mg": 422, "cost_bdt": 15.0},
    {"name": "Papaya", "category": "fruit", "calories": 60, "protein_g": 0.6, "carbs_g": 15.0, "fats_g": 0.4, "sugars_g": 9.0, "sodium_mg": 8, "potassium_mg": 180, "cost_bdt": 20.0},
    {"name": "Milk", "category": "dairy", "calories": 150, "protein_g": 8.0, "carbs_g": 12.0, "fats_g": 8.0, "sugars_g": 12.0, "sodium_mg": 120, "potassium_mg": 380, "cost_bdt": 40.0},
    {"name": "Rosogolla", "category": "sweets", "calories": 150, "protein_g": 2.0, "carbs_g": 32.0, "fats_g": 1.0, "sugars_g": 28.0, "sodium_mg": 60, "potassium_mg": 60, "cost_bdt": 25.0},
    {"name": "Sandesh", "category": "sweets", "calories": 140, "protein_g": 4.0, "carbs_g": 24.0, "fats_g": 4.0, "sugars_g": 18.0, "sodium_mg": 80, "potassium_mg": 120, "cost_bdt": 30.0},
    {"name": "Peanut chaat", "category": "snacks", "calories": 220, "protein_g": 9.0, "carbs_g": 16.0, "fats_g": 14.0, "sugars_g": 3.0, "sodium_mg": 300, "potassium_mg": 200, "cost_bdt": 30.0, "allergens": ["peanut"]},
]

MAX_ITEMS_PER_MEAL = 4
MEAL_CALORIE_TOLERANCE = (0.65, 1.35)


class PlanNotFoundError(Exception):
    """Raised when a patient has no stored meal plan."""


class MealPlanService:
    """Business logic for generating, reading and regenerating meal plans."""

    def __init__(self, session=None):
        self.session = session or SessionLocal()
        self._owns_session = session is None

    def close(self):
        if self._owns_session:
            self.session.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()

    # -- public API -----------------------------------------------------

    def generate_plan(self, patient_id: int, overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Generate (or regenerate) the patient's current meal plan.

        Any previous CURRENT plan is superseded so only one plan per
        patient stays CURRENT at a time.
        """
        overrides = overrides or {}
        patient = self.session.get(User, patient_id)
        if patient is None:
            raise ValueError(f"Patient {patient_id} not found")

        prescriptions = [
            {"medicine_name": p.medicine_name, "notes": p.notes or ""}
            for p in self.session.query(Prescription).filter_by(user_id=patient_id).all()
        ]

        stored = self._current_plan_row(patient_id)
        snapshot = stored.profile_snapshot or {} if stored else {}

        calorie_target = max(
            float(overrides.get("calorie_target", snapshot.get("calorie_target", DEFAULT_CALORIE_TARGET))),
            SAFE_MIN_CALORIES,
        )
        schedule = overrides.get("schedule", snapshot.get("schedule", DEFAULT_SCHEDULE))
        budget = overrides.get("budget", snapshot.get("budget"))
        preferences = [
            str(item).strip().lower()
            for item in (overrides.get("preferences") or snapshot.get("preferences") or [])
            if str(item).strip()
        ]

        restrictions: List[Dict[str, Any]] = []
        for prescription in prescriptions:
            source = prescription["medicine_name"]
            for restriction in extract_prescription_restrictions(prescription["notes"]):
                restriction["source"] = source
                restrictions.append(restriction)

        foods = list(FOOD_CATALOG)
        conflicts = find_dietary_conflicts(prescriptions, foods)
        requires_review = any(conflict.get("requires_review") for conflict in conflicts)

        allowed_foods = self._allowed_foods(foods, restrictions, conflicts)
        slots = MEAL_SCHEDULES[schedule]
        shares = [slot["share"] for slot in slots]
        meals: List[Dict[str, Any]] = []
        for slot, share in zip(slots, shares):
            meal_budget = calorie_target * share
            items = self._pick_items(allowed_foods, meal_budget, preferences)
            meals.append(self._build_meal(slot, items, conflicts))

        actual_macros = self._sum_macros(meals)
        target_macros = split_macros(calorie_target)
        overall_safety = self._overall_safety(meals, conflicts, requires_review)
        estimated_cost = sum(
            item.get("cost_bdt", 0) for meal in meals for item in meal["items"]
        )
        budget_info = {
            "limit": budget,
            "estimated_bdt": round(estimated_cost, 2),
            "within_budget": budget is None or (budget is not None and estimated_cost <= float(budget)),
        }

        profile_snapshot = {
            "calorie_target": calorie_target,
            "schedule": schedule,
            "preferences": preferences,
            "target_macros": target_macros,
            "budget": budget_info,
            "restrictions_applied": [
                {
                    "rule": restriction.get("rule"),
                    "nutrient": restriction.get("nutrient"),
                    "limit": restriction.get("limit"),
                    "explanation": restriction.get("explanation"),
                    "source": restriction.get("source"),
                }
                for restriction in restrictions
            ],
        }
        plan_doc = {
            "schedule": schedule,
            "calorie_target": calorie_target,
            "target_macros": target_macros,
            "actual_macros": actual_macros,
            "meals": meals,
            "safety": {
                "overall": overall_safety,
                "conflicts": conflicts,
                "requires_review": requires_review,
            },
            "budget": budget_info,
        }

        if stored is not None:
            stored.status = STATUS_SUPERSEDED
            stored.updated_at = datetime.now(timezone.utc)
        row = DietPlan(
            user_id=patient_id,
            title=f"Personalized meal plan ({schedule})",
            status=STATUS_CURRENT,
            meal_schedule=schedule,
            calorie_target=calorie_target,
            meal_plan=meals,
            macros=actual_macros,
            profile_snapshot=profile_snapshot,
            restrictions_applied=[restriction.get("rule") for restriction in restrictions],
            conflict_notes=conflicts,
            requires_review=requires_review,
            updated_at=datetime.now(timezone.utc),
        )
        self.session.add(row)
        self.session.commit()
        self.session.refresh(row)
        return self._build_response(row.id, patient_id, plan_doc)

    def get_current_plan(self, patient_id: int) -> Optional[Dict[str, Any]]:
        """Return the patient's current plan, or `None` when none exists."""
        row = self._current_plan_row(patient_id)
        if row is None:
            return None
        plan_doc = {
            "schedule": row.meal_schedule,
            "calorie_target": row.calorie_target,
            "target_macros": (row.profile_snapshot or {}).get(
                "target_macros", split_macros(row.calorie_target or DEFAULT_CALORIE_TARGET)
            ),
            "actual_macros": row.macros or {},
            "meals": row.meal_plan or [],
            "safety": {
                "overall": self._stored_overall_safety(row),
                "conflicts": row.conflict_notes or [],
                "requires_review": bool(row.requires_review),
            },
            "budget": (row.profile_snapshot or {}).get(
                "budget", {"limit": None, "estimated_bdt": 0, "within_budget": True}
            ),
        }
        return self._build_response(row.id, patient_id, plan_doc, generated_at=row.created_at)

    def regenerate_plan(self, patient_id: int, overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Regenerate the plan after goals or preferences change."""
        return self.generate_plan(patient_id, overrides)

    # -- internals ------------------------------------------------------

    def _current_plan_row(self, patient_id: int) -> Optional[DietPlan]:
        return (
            self.session.query(DietPlan)
            .filter_by(user_id=patient_id, status=STATUS_CURRENT)
            .order_by(DietPlan.created_at.desc())
            .first()
        )

    def _allowed_foods(
        self,
        foods: List[Dict[str, Any]],
        restrictions: List[Dict[str, Any]],
        conflicts: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        forbidden = {
            conflict.get("food")
            for conflict in conflicts
            if conflict.get("food")
            and (conflict.get("severity") == SEVERITY_HIGH or conflict.get("requires_review"))
        }
        return [food for food in foods if food.get("name") not in forbidden]

    def _pick_items(
        self,
        allowed_foods: List[Dict[str, Any]],
        meal_budget: float,
        preferences: List[str],
    ) -> List[Dict[str, Any]]:
        """Greedily fill one meal slot within its calorie tolerance band."""
        preferred = [food for food in allowed_foods if any(pref in food["name"].lower() for pref in preferences)]
        ordered = preferred + [food for food in allowed_foods if food not in preferred]
        items: List[Dict[str, Any]] = []
        total = 0.0
        low, high = MEAL_CALORIE_TOLERANCE
        for food in ordered:
            if len(items) >= MAX_ITEMS_PER_MEAL:
                break
            if total + food["calories"] > meal_budget * high:
                continue
            items.append(dict(food))
            total += food["calories"]
            if total >= meal_budget * low:
                break
        if items and total < meal_budget * low:
            for food in ordered:
                if any(item["name"] == food["name"] for item in items):
                    continue
                if total + food["calories"] > meal_budget * high:
                    continue
                items.append(dict(food))
                total += food["calories"]
                if total >= meal_budget * low:
                    break
        return items

    def _build_meal(
        self,
        slot: Dict[str, Any],
        items: List[Dict[str, Any]],
        conflicts: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        calories = sum(item["calories"] for item in items)
        carbs = sum(item["carbs_g"] for item in items)
        protein = sum(item["protein_g"] for item in items)
        fats = sum(item["fats_g"] for item in items)
        meal_foods = [item["name"] for item in items]
        return {
            "time": slot["time"],
            "label": slot["label"],
            "bangla_label": slot.get("bangla_label"),
            "meal_type": slot["meal_type"],
            "calories": round(calories, 1),
            "carbs_g": round(carbs, 1),
            "protein_g": round(protein, 1),
            "fats_g": round(fats, 1),
            "safety_status": self._meal_safety(meal_foods, conflicts),
            "foods": meal_foods,
            "notes": "; ".join(item["name"] for item in items) or "No suitable items found.",
            "items": items,
        }

    def _meal_safety(self, meal_foods: List[str], conflicts: List[Dict[str, Any]]) -> str:
        level = "Safe"
        for conflict in conflicts:
            if conflict.get("food") not in meal_foods:
                continue
            if conflict.get("severity") == SEVERITY_HIGH or conflict.get("requires_review"):
                level = "Avoid"
            elif conflict.get("severity") == "medium" and level == "Safe":
                level = "Caution"
        return level

    def _overall_safety(
        self,
        meals: List[Dict[str, Any]],
        conflicts: List[Dict[str, Any]],
        requires_review: bool,
    ) -> str:
        if requires_review:
            return "Caution"
        if any(meal["safety_status"] == "Avoid" for meal in meals):
            return "Avoid"
        if any(meal["safety_status"] == "Caution" for meal in meals):
            return "Caution"
        return "Safe"

    def _stored_overall_safety(self, row: DietPlan) -> str:
        if row.requires_review:
            return "Caution"
        meals = row.meal_plan or []
        if any(meal.get("safety_status") == "Avoid" for meal in meals):
            return "Avoid"
        if any(meal.get("safety_status") == "Caution" for meal in meals):
            return "Caution"
        return "Safe"

    def _sum_macros(self, meals: List[Dict[str, Any]]) -> Dict[str, float]:
        return {
            "calories": round(sum(meal["calories"] for meal in meals), 1),
            "carbs_g": round(sum(meal["carbs_g"] for meal in meals), 1),
            "protein_g": round(sum(meal["protein_g"] for meal in meals), 1),
            "fats_g": round(sum(meal["fats_g"] for meal in meals), 1),
        }

    def _build_response(
        self,
        plan_id: int,
        patient_id: int,
        plan_doc: Dict[str, Any],
        generated_at: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        # Drop the per-item food objects before the plan leaves the service.
        public_meals = [
            {key: value for key, value in meal.items() if key != "items"} for meal in plan_doc["meals"]
        ]
        return {
            "plan_id": plan_id,
            "status": STATUS_CURRENT,
            "patient_id": patient_id,
            "schedule": plan_doc["schedule"],
            "calorie_target": plan_doc["calorie_target"],
            "target_macros": plan_doc["target_macros"],
            "actual_macros": plan_doc.get("actual_macros") or plan_doc["target_macros"],
            "meals": public_meals,
            "safety": plan_doc["safety"],
            "budget": plan_doc["budget"],
            "generated_at": generated_at or datetime.now(timezone.utc),
            "disclaimer": DECISION_SUPPORT_DISCLAIMER,
        }
