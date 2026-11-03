"""SCRUM-52: Food Safety Checker rules (SRS 3.2.3.1).

Pure function, no database access, so it is easy to test:

    check_food_safety(payload, profile=None, dish=None) -> dict

``payload``  what the user typed on the page (food, allergies, conditions,
             restrictions, portion_limits, preparation_info).
``profile``  the patient's saved health profile (allergies, conditions,
             food_preference); merged with what was typed.
``dish``     the matching Dish DNA record, when one exists (ingredients,
             allergens, diet_tags, sugar_g, glycemic_index, sodium_mg,
             saturated_fat_g).

Result labels follow SRS 3.2.3.1.3: Safe / Caution / Avoid, every Caution or
Avoid carries a reason (3.2.3.1.4), and uncertain or high-risk results are
flagged for professional review instead of claiming certainty (3.2.3.1.5).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from backend.services.plan_generator import _matches_allergen, _words, allergen_terms

SAFE, CAUTION, AVOID = "Safe", "Caution", "Avoid"

MEAT_FISH_EGG_WORDS = (
    "chicken", "beef", "mutton", "meat", "fish", "rui", "ilish", "hilsa",
    "prawn", "shrimp", "crab", "egg", "liver",
)
DAIRY_WORDS = ("milk", "curd", "yogurt", "yoghurt", "paneer", "ghee", "butter", "cheese", "doi")
WHEAT_WORDS = ("wheat", "roti", "paratha", "bread", "flour", "atta", "maida", "biscuit")
FRIED_WORDS = ("fried", "deep fry", "deep-fry", "fry", "bhaja")
RAW_WORDS = ("raw", "undercooked", "half boiled", "half-boiled")


def _as_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = value.replace(";", ",").split(",")
    return [str(v).strip() for v in value if str(v).strip()]


def _num(value: Any) -> Optional[float]:
    try:
        return None if value in (None, "") else float(value)
    except (TypeError, ValueError):
        return None


def _has(text: str, words) -> bool:
    """Whole-word match, so "eggplant" is not "egg" and "coconut milk" is milk."""
    text_words = _words(text)
    for w in words:
        if " " in w or "-" in w:
            if w in text:
                return True
        elif any(t == w or t.endswith(w) for t in text_words):
            return True
    return False


def check_food_safety(
    payload: Optional[Dict[str, Any]],
    profile: Optional[Dict[str, Any]] = None,
    dish: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    payload = payload if isinstance(payload, dict) else {}
    profile = profile or {}
    food = str(payload.get("food", "")).strip()
    if not food:
        return {
            "food": "",
            "status": "error",
            "message": "Food name is required.",
            "reasons": ["Please type the name of a food."],
            "professional_review": False,
            "matched_dish": None,
        }

    # Merge what was typed with the saved health profile (no duplicates).
    allergies = list(dict.fromkeys(_as_list(payload.get("allergies")) + _as_list(profile.get("allergies"))))
    conditions = list(dict.fromkeys(_as_list(payload.get("conditions")) + _as_list(profile.get("conditions"))))
    restrictions = list(dict.fromkeys(
        _as_list(payload.get("restrictions")) + _as_list(profile.get("food_preference"))
    ))
    restrictions = [r for r in restrictions if r.lower() not in ("no restriction", "none")]

    portion = payload.get("portion_limits") or {}
    portion_note = portion.get("notes", "") if isinstance(portion, dict) else str(portion)
    preparation = str(payload.get("preparation_info") or "").strip()

    dish = dish or {}
    contents = " ".join([
        food,
        str(dish.get("ingredients") or ""),
        ", ".join(_as_list(dish.get("allergens"))),
    ]).lower()
    diet_tags = " ".join(_as_list(dish.get("diet_tags"))).lower()

    avoid: List[str] = []
    caution: List[str] = []

    # 1. Allergies -> Avoid (high risk).
    for label, matches in allergen_terms(", ".join(allergies)):
        if _matches_allergen(contents, [(label, matches)]):
            avoid.append(f"Contains or may contain {label.lower()}, which is in your allergy list.")

    # 2. Food preference / dietary restrictions.
    cond_text = " ".join(conditions).lower()
    rest_text = " ".join(restrictions).lower().replace("-", " ")
    is_non_veg = "non veg" in diet_tags.replace("-", " ") or _has(contents, MEAT_FISH_EGG_WORDS)
    if "vegan" in rest_text:
        if is_non_veg or _has(contents, DAIRY_WORDS):
            avoid.append("Has meat, fish, egg or dairy, but your preference is vegan.")
    elif "vegetarian" in rest_text and "non vegetarian" not in rest_text and is_non_veg:
        avoid.append("Has meat, fish or egg, but your preference is vegetarian.")
    if ("gluten" in rest_text or "celiac" in cond_text) and _has(contents, WHEAT_WORDS):
        avoid.append("Has wheat (gluten), but you follow a gluten-free diet.")
    if ("dairy free" in rest_text or "lactose" in rest_text) and _has(contents, DAIRY_WORDS):
        avoid.append("Has dairy, but you follow a dairy-free / lactose-free diet.")

    # 3. Health conditions vs. nutrition (only when Dish DNA data exists).
    gi, sugar = _num(dish.get("glycemic_index")), _num(dish.get("sugar_g"))
    sodium, sat_fat = _num(dish.get("sodium_mg")), _num(dish.get("saturated_fat_g"))
    if "diabet" in cond_text:
        if gi is not None and gi >= 70:
            caution.append(f"High glycemic index ({gi:.0f}) can raise blood sugar quickly; keep the portion small.")
        if sugar is not None and sugar >= 15:
            caution.append(f"{sugar:.0f} g sugar per serving is high for diabetes.")
    if any(k in cond_text for k in ("hypertens", "blood pressure", "kidney", "renal")) and sodium is not None and sodium >= 600:
        caution.append(f"{sodium:.0f} mg sodium per serving is high for your condition.")
    if any(k in cond_text for k in ("heart", "cardiac", "cholesterol")) and sat_fat is not None and sat_fat >= 5:
        caution.append(f"{sat_fat:.1f} g saturated fat per serving; choose a smaller portion.")

    # 4. Preparation and portion information.
    prep = preparation.lower()
    if prep and _has(prep, FRIED_WORDS) and any(k in cond_text for k in ("heart", "cardiac", "cholesterol", "diabet", "hypertens")):
        caution.append("Fried preparation adds fat; prefer boiled, steamed or grilled.")
    if prep and _has(prep, RAW_WORDS) and _has(contents, ("fish", "egg", "meat", "chicken", "prawn")):
        caution.append("Raw or undercooked fish, egg or meat carries an infection risk; cook it thoroughly.")
    if portion_note:
        caution.append(f"Follow your portion limit: {portion_note}")

    # 5. Decide the label.
    reasons: List[str] = []
    if avoid:
        status, review = AVOID, True
        reasons = avoid + caution
        message = "Avoid this food. It conflicts with your allergies or diet."
    elif not dish:
        status, review = CAUTION, True
        reasons = caution + [
            f"'{food}' is not in the Dish DNA database yet, so its ingredients could not be verified. "
            "Check with a doctor or nutritionist."
        ]
        message = "Not enough data to call this safe. Please confirm with a professional."
    elif caution:
        status, review = CAUTION, False
        reasons = caution
        message = "You can have this with care. See the reasons below."
    else:
        status, review = SAFE, False
        reasons = ["No conflict found with your allergies, diet or recorded conditions."]
        message = "This food looks safe for you based on your saved information."

    return {
        "food": food,
        "status": status,
        "message": message,
        "reasons": reasons,
        "professional_review": review,
        "matched_dish": dish.get("name") if dish else None,
        "checked_against": {"allergies": allergies, "conditions": conditions, "restrictions": restrictions},
    }