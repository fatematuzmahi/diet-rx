def check_food_safety(payload):
    food = payload.get("food", "").strip()
    if not food:
        return {"message": "Food name is required.", "status": "error"}
    return {"food": food, "status": "caution", "message": "Starter result. Add verified condition, allergy, and prescription rules."}
