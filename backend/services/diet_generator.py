#: Below this calorie target the generator refuses to go: very low
#: calorie diets are unsafe without clinical supervision.
SAFE_MIN_CALORIES = 1200.0

#: Default macro split (share of calories): 50 % carbs, 30 % protein,
#: 20 % fat.
DEFAULT_MACRO_SPLIT = {"carbs": 0.50, "protein": 0.30, "fats": 0.20}

#: Calories per gram for each macro.
CALORIES_PER_GRAM = {"carbs": 4.0, "protein": 4.0, "fat": 9.0}


def split_macros(calories: float, split: dict | None = None) -> dict:
    """Return macro gram targets for a daily calorie target."""
    split = split or DEFAULT_MACRO_SPLIT
    macros: dict = {"calories": round(calories, 1)}
    calorie_key_map = {"fat": "fat", "fats": "fat"}  # map macro names to CALORIES_PER_GRAM keys
    for macro, share in split.items():
        gram_key = macro + "_g"  # convert "carbs" -> "carbs_g", etc.
        per_gram_key = calorie_key_map.get(macro, macro)  # fallback to macro name
        macros[gram_key] = round((calories * share) / CALORIES_PER_GRAM[per_gram_key], 1)
    return macros


def meal_calories(target_calories: float, schedule_shares: list) -> list:
    """Split a daily calorie target across meal slots by share."""
    return [round(target_calories * share, 1) for share in schedule_shares]


def generate_diet_plan(profile):
    """Build macro targets from a patient profile.

    ``profile`` may carry ``calorie_target``; anything missing falls back
    to a safe default. The returned calorie target is never below
    ``SAFE_MIN_CALORIES``.
    """
    target = float(profile.get("calorie_target") or 2000.0)
    target = max(target, SAFE_MIN_CALORIES)
    return {
        "calorie_target": target,
        "target_macros": split_macros(target),
    }
