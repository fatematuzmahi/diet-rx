def check_food_safety(payload):
    """Evaluate food against user-provided safety information."""

    payload = payload if isinstance(payload, dict) else {}

    food = str(payload.get("food", "")).strip()
    if not food:
        return {
            "food": "",
            "status": "Avoid",
            "message": "Food name is required.",
            "reasons": ["Please provide a food name."],
            "professional_review": False,
        }

    conditions = payload.get("conditions", [])
    allergies = payload.get("allergies", [])
    restrictions = payload.get("restrictions", [])
    portion_limits = payload.get("portion_limits", {})
    preparation_info = payload.get("preparation_info", "")

    # Normalize user-provided lists.
    if isinstance(conditions, str):
        conditions = [conditions]
    if isinstance(allergies, str):
        allergies = [allergies]
    if isinstance(restrictions, str):
        restrictions = [restrictions]

    reasons = []
    status = "Caution"
    professional_review = False

    # This initial service does not contain a verified medical rules database.
    reasons.append(
        "A verified safety-rule check is not yet available; "
        "confirm suitability with a qualified professional."
    )
    professional_review = True

    if allergies:
        reasons.append(
            "Check every ingredient for the reported allergies: "
            + ", ".join(map(str, allergies))
        )

    if conditions:
        reasons.append(
            "Food suitability may depend on these health conditions: "
            + ", ".join(map(str, conditions))
        )

    if restrictions:
        reasons.append(
            "Check the food against these dietary restrictions: "
            + ", ".join(map(str, restrictions))
        )

    if portion_limits:
        reasons.append("Review the supplied portion limits before serving.")

    if preparation_info:
        reasons.append(
            "Preparation information supplied: " + str(preparation_info)
        )

    # Explicit allergy matches require avoidance and professional guidance.
    food_lower = food.casefold()
    matched_allergies = [
        str(item).strip()
        for item in allergies
        if str(item).strip().casefold() in food_lower
    ]

    if matched_allergies:
        status = "Avoid"
        reasons.append(
            "The food name matches a reported allergy: "
            + ", ".join(matched_allergies)
        )
    else:
        status = "Caution"

    return {
        "food": food,
        "status": status,
        "message": (
            "Potential allergy match found. Avoid until confirmed safe."
            if matched_allergies
            else "Preliminary result only; safety has not been verified."
        ),
        "reasons": reasons,
        "professional_review": professional_review,
    }
