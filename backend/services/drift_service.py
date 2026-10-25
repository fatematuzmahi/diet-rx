"""SCRUM-55: Dietary drift detection + adaptive compensator (SRS 3.2.3.4).

Pure functions, no database, so every rule can be unit-tested:

    detect_and_compensate(meals, entries, now_hhmm, calorie_target,
                          conditions, allergies, dishes) -> dict

* ``meals``    today's meals from the CURRENT plan
               ({meal_type, time "HH:MM", items, calories}).
* ``entries``  today's Daily Log entries (only MEAL entries are used).
* ``dishes``   Dish DNA facts for logged dishes: {dish_id: {"calories", "allergens"}}.

Rules (SRS 3.2.3.4.1 - 3.2.3.4.5)
---------------------------------
1. Drift detection
   * SKIPPED      a planned meal whose time + 90 minutes has passed with nothing logged.
   * SUBSTITUTED  something other than the planned meal was logged for that meal.
   * EXTRA        food logged for a meal that was already eaten.
2. Recalculation: the calories still allowed today are spread over the meals
   that are still to come, in proportion to their planned size.
3. Safety limits / restriction guard
   * a meal may change by at most 25 % (15 % with diabetes) of its planned size;
     a bigger change is capped at that limit and shown as a suggestion only;
   * with pregnancy a meal is never made smaller;
   * only the portion of foods ALREADY in the plan changes - no new food is
     added - so allergy, food-preference and prescription filtering stays intact;
   * a logged food that matches a recorded allergy raises a HIGH alert.
4. Every changed meal carries a plain-language explanation.
5. A change bigger than the limit (or unknown calories) is shown as a
   suggestion only. An allergy hit, eating more than 125 % of the day's target,
   or (with diabetes) a change more than twice the limit asks for professional
   review. Only "adjusted" days are within the limit.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from backend.services.plan_generator import _matches_allergen, allergen_terms

PLAN_NOTE_PREFIX = "From today's plan: "
GRACE_MINUTES = 90
MAX_CHANGE = 0.25
MAX_CHANGE_DIABETES = 0.15
ON_TRACK_TOLERANCE = 0.05

SLOT_TO_MEALS = {
    "BREAKFAST": ("breakfast",),
    "LUNCH": ("lunch",),
    "DINNER": ("dinner",),
    "SNACK": ("mid-morning snack", "afternoon snack", "snack"),
}

DISCLAIMER = (
    "These adjustments are decision support based on your own logs. They are not "
    "medical advice. Follow your doctor's or dietitian's instructions."
)


def _minutes(hhmm: Optional[str]) -> Optional[int]:
    try:
        h, m = str(hhmm).strip()[:5].split(":")
        return int(h) * 60 + int(m)
    except (ValueError, AttributeError):
        return None


def _num(value) -> Optional[float]:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _qty(entry: Dict[str, Any]) -> float:
    q = _num(entry.get("quantity"))
    return q if q and q > 0 else 1.0


def _flag(conditions: str, *words: str) -> bool:
    text = (conditions or "").lower()
    return any(w in text for w in words)


def _match_meal(entry, meals, state) -> Optional[int]:
    """Which planned meal does a logged meal belong to?"""
    notes = str(entry.get("notes") or "")
    if notes.startswith(PLAN_NOTE_PREFIX):
        wanted = notes[len(PLAN_NOTE_PREFIX):].strip().lower()
        for i, meal in enumerate(meals):
            if str(meal.get("meal_type", "")).lower() == wanted:
                return i
    names = SLOT_TO_MEALS.get(str(entry.get("slot") or "").upper(), ())
    candidates = [i for i, m in enumerate(meals) if str(m.get("meal_type", "")).lower() in names]
    if not candidates:
        return None
    logged_at = _minutes(entry.get("log_time"))
    # Prefer a candidate meal that is not eaten yet, then the one closest in time.
    def rank(i):
        taken = state[i]["state"] in ("eaten", "substituted")
        gap = abs((_minutes(meals[i].get("time")) or 0) - logged_at) if logged_at is not None else 0
        return (taken, gap)
    return sorted(candidates, key=rank)[0]


def detect_and_compensate(
    meals: List[Dict[str, Any]],
    entries: Iterable[Dict[str, Any]],
    now_hhmm: str,
    calorie_target: Optional[float] = None,
    conditions: Optional[str] = None,
    allergies: Optional[str] = None,
    dishes: Optional[Dict[int, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    dishes = dishes or {}
    now = _minutes(now_hhmm)
    if now is None:
        now = 0
    planned_total = sum(_num(m.get("calories")) or 0 for m in meals)
    target = _num(calorie_target) or planned_total

    diabetes = _flag(conditions, "diabet")
    pregnant = _flag(conditions, "pregnan")
    limit = MAX_CHANGE_DIABETES if diabetes else MAX_CHANGE
    terms = allergen_terms(allergies)

    state = [{"state": "pending", "actual_kcal": None, "logged": []} for _ in meals]
    events: List[Dict[str, Any]] = []
    consumed = 0.0
    unknown: List[str] = []

    # ---------------------------------------------------------------- logs
    for entry in entries:
        if entry.get("log_type") != "MEAL":
            continue
        title = (entry.get("title") or "a meal").strip()
        qty = _qty(entry)
        idx = _match_meal(entry, meals, state)
        from_plan = str(entry.get("notes") or "").startswith(PLAN_NOTE_PREFIX)

        kcal: Optional[float]
        if from_plan and idx is not None:
            kcal = (_num(meals[idx].get("calories")) or 0) * qty
        else:
            dish = dishes.get(entry.get("dish_id")) if entry.get("dish_id") is not None else None
            dish_kcal = _num(dish.get("calories")) if dish else None
            kcal = dish_kcal * qty if dish_kcal is not None else None

        if kcal is None:
            unknown.append(title)
        else:
            consumed += kcal

        # Allergy guard on what was actually eaten.
        dish = dishes.get(entry.get("dish_id")) if entry.get("dish_id") is not None else None
        allergen_text = f"{title} {(dish or {}).get('allergens') or ''}"
        hits = [label for label, m in terms if _matches_allergen(allergen_text, [(label, m)])]
        if hits:
            events.append({
                "type": "allergy", "severity": "HIGH", "meal_type": meals[idx]["meal_type"] if idx is not None else None,
                "title": title, "planned_kcal": None, "actual_kcal": kcal, "delta_kcal": None,
                "message": f"“{title}” may contain {', '.join(h.lower() for h in hits)}, which is in your allergy list. "
                           "If you feel unwell, contact your doctor or local emergency services.",
            })

        if idx is None:
            events.append({"type": "extra", "severity": "INFO", "meal_type": None, "title": title,
                           "planned_kcal": 0, "actual_kcal": kcal, "delta_kcal": kcal,
                           "message": f"Extra food outside your plan: {title}."})
            continue

        meal = meals[idx]
        planned = _num(meal.get("calories")) or 0
        slot = state[idx]
        if slot["state"] in ("eaten", "substituted"):
            events.append({"type": "extra", "severity": "INFO", "meal_type": meal["meal_type"], "title": title,
                           "planned_kcal": 0, "actual_kcal": kcal, "delta_kcal": kcal,
                           "message": f"Extra food at {meal['meal_type'].lower()}: {title}."})
            slot["actual_kcal"] = (slot["actual_kcal"] or 0) + (kcal or 0)
        elif from_plan:
            slot["state"] = "eaten"
            slot["actual_kcal"] = kcal
            if qty != 1:
                events.append({"type": "extra" if qty > 1 else "substituted", "severity": "INFO",
                               "meal_type": meal["meal_type"], "title": title, "planned_kcal": planned,
                               "actual_kcal": kcal, "delta_kcal": kcal - planned,
                               "message": f"{meal['meal_type']}: {qty:g} portions instead of 1."})
        else:
            slot["state"] = "substituted"
            slot["actual_kcal"] = kcal
            delta = None if kcal is None else kcal - planned
            what = "calories unknown" if kcal is None else f"{kcal:.0f} kcal instead of {planned:.0f}"
            events.append({"type": "substituted", "severity": "INFO", "meal_type": meal["meal_type"], "title": title,
                           "planned_kcal": planned, "actual_kcal": kcal, "delta_kcal": delta,
                           "message": f"{meal['meal_type']}: you had {title} instead of the planned meal ({what})."})
        slot["logged"].append(title)

    # ------------------------------------------------------------- skipped
    for i, meal in enumerate(meals):
        start = _minutes(meal.get("time"))
        if state[i]["state"] == "pending" and start is not None and now >= start + GRACE_MINUTES:
            state[i]["state"] = "skipped"
            planned = _num(meal.get("calories")) or 0
            events.append({"type": "skipped", "severity": "CAUTION" if diabetes else "INFO",
                           "meal_type": meal["meal_type"], "title": None, "planned_kcal": planned,
                           "actual_kcal": 0, "delta_kcal": -planned,
                           "message": f"{meal['meal_type']} ({meal.get('time')}) was not logged, so it counts as skipped."})

    # -------------------------------------------------------- recalculate
    pending = [i for i, s in enumerate(state) if s["state"] == "pending"]
    pending_planned = sum(_num(meals[i].get("calories")) or 0 for i in pending)
    remaining = target - consumed
    drift_events = [e for e in events if e["type"] in ("skipped", "substituted", "extra")]
    allergy_hit = any(e["type"] == "allergy" for e in events)

    factor = 1.0
    needed = 1.0
    if pending and pending_planned > 0:
        needed = max(remaining, 0) / pending_planned
    change = needed - 1.0

    notes: List[str] = []
    big_overeat = target > 0 and consumed > target * (1 + MAX_CHANGE)
    if not pending:
        status = "professional_review" if big_overeat else "day_complete"
    elif unknown:
        status = "suggestion_only"
        notes.append("Calories are unknown for: " + ", ".join(unknown) +
                     ". Pick the dish from Dish DNA when you log it so DietRx can rebalance your day.")
    elif not drift_events or abs(change) < ON_TRACK_TOLERANCE:
        status = "on_track"
    else:
        low = 1.0 if pregnant else 1.0 - limit
        factor = min(max(needed, low), 1.0 + limit)
        if abs(change) <= limit + 1e-9 and not (pregnant and needed < 1.0):
            status = "adjusted"
        else:
            # Beyond the safe limit: show the capped change as a suggestion only.
            status = "suggestion_only"
            if needed > 1.0 + limit:
                notes.append("Don't try to make up the whole difference. Eating much more later "
                             "in the day is not safer than a missed meal.")
            elif needed < 1.0 - limit:
                notes.append("Don't skip the next meals to make up for it. A smaller portion is enough.")
        if big_overeat or (diabetes and abs(change) > 2 * limit):
            status = "professional_review"
    if allergy_hit:
        status = "professional_review"
    if pregnant and needed < 1.0 and pending:
        notes.append("With pregnancy, DietRx never makes a meal smaller. Talk to your doctor before eating less.")
    if diabetes and any(e["type"] == "skipped" for e in events):
        notes.append("With diabetes, try not to skip meals. Extra food is spread out instead of one big meal.")

    apply = status in ("adjusted", "suggestion_only") or (status == "professional_review" and not allergy_hit)
    reason = "; ".join(
        f"{e['meal_type'] or 'extra food'} {e['type']}" + (f" ({e['delta_kcal']:+.0f} kcal)" if e.get("delta_kcal") is not None else "")
        for e in drift_events
    )

    out_meals = []
    for i, meal in enumerate(meals):
        planned = _num(meal.get("calories")) or 0
        item = {
            "meal_type": meal.get("meal_type"), "time": meal.get("time"), "items": list(meal.get("items") or []),
            "planned_kcal": round(planned), "state": state[i]["state"],
            "actual_kcal": None if state[i]["actual_kcal"] is None else round(state[i]["actual_kcal"]),
            "logged": state[i]["logged"], "adjusted_kcal": None, "change_percent": 0, "explanation": None,
        }
        if item["state"] == "pending" and apply and abs(factor - 1.0) >= ON_TRACK_TOLERANCE:
            pct = round((factor - 1.0) * 100)
            item["adjusted_kcal"] = round(planned * factor)
            item["change_percent"] = pct
            word = "larger" if pct > 0 else "smaller"
            item["explanation"] = (
                f"Because of today's changes ({reason}), have about {abs(pct)}% {word} portions of the same foods: "
                f"{planned:.0f} → {planned * factor:.0f} kcal. No new food is added, so your allergy and diet rules still apply."
            )
        out_meals.append(item)

    messages = {
        "on_track": "You're on track. No change is needed for the rest of today.",
        "adjusted": "Your remaining meals were gently rebalanced within safe limits.",
        "suggestion_only": "The change needed is bigger than the safe limit or not fully known, "
                           "so DietRx does not change your plan automatically.",
        "professional_review": "Today is very different from your plan. Please check with your doctor or "
                               "dietitian before changing today's meals; the numbers below are only a suggestion.",
        "day_complete": "All of today's meals are done. Tomorrow starts fresh from your plan.",
    }

    return {
        "status": status,
        "message": messages[status],
        "professional_review": status == "professional_review",
        "calorie_target": round(target),
        "consumed_kcal": round(consumed),
        "remaining_kcal": round(max(remaining, 0)),
        "over_target_kcal": round(max(-remaining, 0)),
        "needed_change_percent": round(change * 100) if pending else 0,
        "applied_change_percent": round((factor - 1.0) * 100) if apply else 0,
        "limit_percent": round(limit * 100),
        "unknown_calories": unknown,
        "events": events,
        "meals": out_meals,
        "notes": notes,
        "disclaimer": DISCLAIMER,
    }
