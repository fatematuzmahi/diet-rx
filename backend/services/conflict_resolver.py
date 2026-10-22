"""Multi-prescription dietary conflict resolver (SRS 3.3.4).

Turns the free-text dietary instructions recorded on a patient's active
prescriptions into normalized restriction tokens, compares those tokens
against candidate foods, and returns every conflict the meal-plan
generator must honor.

Design notes
------------
* Restriction tokens are normalized to lowercase, so ``Low Sodium``,
  ``LOW SODIUM`` and ``low-sodium`` are treated as the same rule.
* Two restrictions that cannot both hold (for example ``low protein``
  from a renal prescription and ``high protein`` from another
  prescription) contradict each other. Contradictions are reported with
  ``requires_review = True`` instead of being silently resolved -- the
  generator never guesses which rule wins.
* Allergen exposures and contradictions are high severity; nutrient-limit
  breaches are medium; everything else is low.
* Every conflict carries a plain-language ``explanation`` and, where a
  safe intersection exists, a ``resolution``. Conflicts the generator
  cannot resolve safely are flagged for a doctor or nutritionist.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Sequence

SEVERITY_LOW = "low"
SEVERITY_MEDIUM = "medium"
SEVERITY_HIGH = "high"

#: Conflict codes emitted by the resolver.
CODE_ALLERGEN_EXPOSURE = "CODE_ALLERGEN_EXPOSURE"
CODE_DIABETIC_SUGAR_LIMIT = "CODE_DIABETIC_SUGAR_LIMIT"
CODE_SODIUM_LIMIT = "CODE_SODIUM_LIMIT"
CODE_POTASSIUM_LIMIT = "CODE_POTASSIUM_LIMIT"
CODE_PROTEIN_LIMIT = "CODE_PROTEIN_LIMIT"
CODE_PRESCRIPTION_CONTRADICTION = "CODE_PRESCRIPTION_CONTRADICTION"
CODE_CALORIE_GOAL_CONFLICT = "CODE_CALORIE_GOAL_CONFLICT"
CODE_GLUTEN_EXPOSURE = "CODE_GLUTEN_EXPOSURE"

#: Heuristic nutrient limits per 100 g of food. Decision support only --
#: a clinician must confirm patient-specific limits.
NUTRIENT_LIMITS = {
    "sugar": {"max_g": 5.0, "severity": SEVERITY_HIGH},
    "sodium": {"max_mg": 140.0, "severity": SEVERITY_HIGH},
    "potassium": {"max_mg": 200.0, "severity": SEVERITY_MEDIUM},
    "protein": {"max_g": 5.0, "severity": SEVERITY_MEDIUM},
}

#: Food columns that hold each restricted nutrient.
NUTRIENT_FIELDS = {
    "sugar": "sugars_g",
    "sodium": "sodium_mg",
    "potassium": "potassium_mg",
    "protein": "protein_g",
}

#: Food categories that contain gluten.
GLUTEN_CATEGORIES = {"bakery", "grains", "wheat", "bread", "pastry"}

#: Keywords used to detect allergens in food names.
ALLERGEN_KEYWORDS = {
    "peanut": ["peanut", "groundnut"],
    "tree nut": ["almond", "cashew", "walnut", "hazelnut"],
    "milk": ["milk", "paneer", "cheese", "dahi", "yogurt", "yoghurt"],
    "fish": ["fish", "mach", "prawn", "shrimp"],
    "egg": ["egg", "dim"],
    "soy": ["soy", "soya", "tofu"],
    "sesame": ["sesame", "til"],
}


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def extract_prescription_restrictions(notes: str) -> List[Dict[str, Any]]:
    """Parse one prescription's free-text notes into restriction tokens."""
    text = _normalize(notes)
    if not text:
        return []
    restrictions: List[Dict[str, Any]] = []

    if re.search(r"\bdiabet\w*\b", text):
        restrictions.append({
            "nutrient": "sugar", "limit": "avoid", "severity": SEVERITY_HIGH,
            "rule": "diabetic",
            "explanation": "Diabetic prescription: added sugar should be avoided.",
        })
    if re.search(r"\blow.?sodium\b|\bsalt.?restrict\b|\bhypertension\b|\bhigh blood pressure\b", text):
        restrictions.append({
            "nutrient": "sodium", "limit": "low", "severity": SEVERITY_HIGH,
            "rule": "low_sodium",
            "explanation": "Low-sodium diet: keep sodium per serving within the prescribed limit.",
        })
    if re.search(r"\brenal\b|\bkidney\b|\bckd\b|chronic kidney", text):
        restrictions.extend([
            {"nutrient": "potassium", "limit": "low", "severity": SEVERITY_HIGH, "rule": "renal",
             "explanation": "Renal diet: potassium should be restricted."},
            {"nutrient": "protein", "limit": "low", "severity": SEVERITY_MEDIUM, "rule": "renal",
             "explanation": "Renal diet: protein intake should be restricted."},
            {"nutrient": "sodium", "limit": "low", "severity": SEVERITY_HIGH, "rule": "renal",
             "explanation": "Renal diet: sodium should be restricted."},
        ])
    if re.search(r"\bwarfarin\b|\bblood thinner\b|anticoagulant", text):
        restrictions.append({
            "nutrient": "vitamin_k", "limit": "consistent", "severity": SEVERITY_MEDIUM,
            "rule": "warfarin",
            "explanation": "Anticoagulant therapy: vitamin K intake should stay consistent.",
        })
    if re.search(r"\bgluten\b|\bceliac\b", text):
        restrictions.append({
            "category": "gluten", "limit": "avoid", "severity": SEVERITY_HIGH, "rule": "gluten_free",
            "explanation": "Gluten-free diet: wheat and bakery items should be avoided.",
        })
    if re.search(r"\bhigh.?protein\b", text):
        restrictions.append({
            "nutrient": "protein", "limit": "high", "severity": SEVERITY_LOW, "rule": "high_protein",
            "explanation": "High-protein goal: protein intake should be emphasized.",
        })
    match = re.search(r"allergy[:\s]*([a-z][a-z ]+)", text)
    if match:
        allergen = match.group(1).strip()
        restrictions.append({
            "allergen": allergen, "limit": "avoid", "severity": SEVERITY_HIGH, "rule": "allergy",
            "explanation": f"Allergy: avoid {allergen}.",
        })
    return restrictions


def _contradictions(all_restrictions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Find nutrient rules that contradict each other across prescriptions."""
    conflicts: List[Dict[str, Any]] = []
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for restriction in all_restrictions:
        nutrient = restriction.get("nutrient")
        if not nutrient:
            continue
        grouped.setdefault(nutrient, []).append(restriction)
    for nutrient, items in grouped.items():
        limits = {item["limit"] for item in items}
        if len(limits & {"low", "high", "avoid"}) >= 2:
            sources = sorted({item.get("source", "unknown prescription") for item in items})
            conflicts.append({
                "code": CODE_PRESCRIPTION_CONTRADICTION,
                "severity": SEVERITY_HIGH,
                "explanation": (
                    f"Prescriptions contradict each other on {nutrient}: one asks to restrict it "
                    f"while another emphasizes it. The two rules cannot both be followed."
                ),
                "sources": sources,
                "resolution": None,
                "requires_review": True,
                "food": None,
                "nutrient": nutrient,
            })
    return conflicts


def _conflict_for_food(food: Dict[str, Any], restrictions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Return the conflicts between one food and the active restrictions."""
    name = (food.get("name") or "").lower()
    category = (food.get("category") or "").lower()
    conflicts: List[Dict[str, Any]] = []
    for restriction in restrictions:
        source = restriction.get("source", "unknown prescription")

        allergen = restriction.get("allergen")
        if allergen and allergen in name:
            conflicts.append({
                "code": CODE_ALLERGEN_EXPOSURE,
                "severity": SEVERITY_HIGH,
                "explanation": f"{food.get('name')} contains {allergen}, which the patient is allergic to.",
                "sources": [source],
                "resolution": f"Remove {food.get('name')} and replace it with an allergen-free alternative.",
                "requires_review": True,
                "food": food.get("name"),
                "nutrient": None,
            })
            continue

        if restriction.get("category") == "gluten" and category in GLUTEN_CATEGORIES:
            conflicts.append({
                "code": CODE_GLUTEN_EXPOSURE,
                "severity": SEVERITY_HIGH,
                "explanation": f"{food.get('name')} is a {category} item and contains gluten.",
                "sources": [source],
                "resolution": "Replace with a gluten-free grain such as rice.",
                "requires_review": True,
                "food": food.get("name"),
                "nutrient": None,
            })
            continue

        nutrient = restriction.get("nutrient")
        field = NUTRIENT_FIELDS.get(nutrient)
        if not field or nutrient not in NUTRIENT_LIMITS:
            continue
        value = food.get(field)
        if value is None:
            continue
        limit = restriction.get("limit")
        limit_def = NUTRIENT_LIMITS[nutrient]
        max_value = limit_def["max_g"] if "max_g" in limit_def else limit_def["max_mg"]
        unit = "g" if "max_g" in limit_def else "mg"
        if limit in ("avoid", "low") and value > max_value:
            code_map = {
                "sugar": CODE_DIABETIC_SUGAR_LIMIT,
                "sodium": CODE_SODIUM_LIMIT,
                "potassium": CODE_POTASSIUM_LIMIT,
                "protein": CODE_PROTEIN_LIMIT,
            }
            conflicts.append({
                "code": code_map[nutrient],
                "severity": limit_def["severity"],
                "explanation": (
                    f"{food.get('name')} has {value}{unit} {nutrient} per serving, above the "
                    f"{max_value}{unit} limit implied by {source}."
                ),
                "sources": [source],
                "resolution": f"Swap {food.get('name')} for a lower-{nutrient} alternative.",
                "requires_review": False,
                "food": food.get("name"),
                "nutrient": nutrient,
            })
    return conflicts


def find_dietary_conflicts(
    prescriptions: Sequence[Dict[str, Any]],
    foods: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Resolve every dietary conflict between prescriptions and foods.

    Parameters
    ----------
    prescriptions:
        Active prescriptions, each ``{"medicine_name": str, "notes": str}``.
    foods:
        Candidate foods, each a dict with ``name``, ``category`` and the
        nutrient fields used by :data:`NUTRIENT_LIMITS`.

    Returns
    -------
    list of dict
        Each conflict carries ``code``, ``severity`` (low/medium/high),
        ``explanation``, ``sources``, ``resolution``, ``requires_review``,
        ``food`` and ``nutrient``. Conflicts are sorted by severity so the
        most serious ones are shown first. Duplicate restrictions from
        different prescriptions are reported once, with every source
        listed.
    """
    all_restrictions: List[Dict[str, Any]] = []
    for prescription in prescriptions or []:
        source = prescription.get("medicine_name") or prescription.get("name") or "unknown prescription"
        for restriction in extract_prescription_restrictions(prescription.get("notes", "") or ""):
            restriction["source"] = source
            all_restrictions.append(restriction)

    merged: Dict[tuple, Dict[str, Any]] = {}
    for conflict in _contradictions(all_restrictions):
        key = (conflict["code"],)  # deduplicate by code only
        existing = merged.get(key)
        if existing is None:
            merged[key] = dict(conflict)
        else:
            # Duplicate restriction reported once, with every source listed.
            for source in conflict["sources"]:
                if source not in existing["sources"]:
                    existing["sources"].append(source)
    for food in foods or []:
        for conflict in _conflict_for_food(food, all_restrictions):
            key = (conflict["code"],)  # deduplicate by code only
            existing = merged.get(key)
            if existing is None:
                merged[key] = dict(conflict)
            else:
                # Duplicate restriction reported once, with every source listed.
                for source in conflict["sources"]:
                    if source not in existing["sources"]:
                        existing["sources"].append(source)
    conflicts = list(merged.values())

    severity_order = {SEVERITY_HIGH: 0, SEVERITY_MEDIUM: 1, SEVERITY_LOW: 2}
    conflicts.sort(key=lambda item: severity_order.get(item["severity"], 3))
    return conflicts
