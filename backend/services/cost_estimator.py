"""Pure cost calculations for SCRUM-58.

Prices must come from FOOD_PRICE; this module never invents prices.

Every plan item is priced with ``grocery_service.price_plan_item`` -- the same
exact-name rules as the grocery list (SCRUM-60) and the patient's saved swaps
(SCRUM-59) -- so the cost estimate and the grocery list always agree.

A meal, a day or the whole plan whose items are not all priced is marked as
partial: a missing price is never shown as 0 and never makes the budget look
safer than it is.
"""

from collections import Counter
from datetime import date, datetime
from typing import Any, Dict, Optional, Sequence

from backend.services.grocery_service import price_plan_item


def _date_value(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _money(value) -> Optional[int]:
    return None if value is None else int(round(float(value)))


def _budget_summary(per_month: float, complete: bool, priced_any: bool,
                    monthly_budget: Optional[float], budget: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Compare the estimate with the patient's budget (any SCRUM-57 budget mode)."""
    if budget and budget.get("found") and budget.get("per_month"):
        monthly = float(budget["per_month"])
        per_day = float(budget.get("per_day") or monthly / 30)
        mode, amount = budget.get("mode"), budget.get("amount")
    elif monthly_budget:
        monthly, per_day, mode, amount = float(monthly_budget), float(monthly_budget) / 30, "MONTHLY", float(monthly_budget)
    else:
        return {"monthly_budget_bdt": None, "status": "not_set", "difference_bdt": None,
                "used_percent": None, "budget_per_day_bdt": None, "mode": None, "amount": None}

    result = {"monthly_budget_bdt": _money(monthly), "budget_per_day_bdt": _money(per_day),
              "mode": mode, "amount": amount}
    if not priced_any:
        result.update(status="unknown", difference_bdt=None, used_percent=None)
        return result
    difference = monthly - per_month
    if difference < 0:
        status = "above"  # already above, even before the missing prices are added
    else:
        # Missing prices can only add cost, so "within" is certain only when complete.
        status = "within" if complete else "within_so_far"
    result.update(status=status, difference_bdt=_money(difference),
                  used_percent=round(per_month / monthly * 100) if monthly > 0 else None)
    return result


def estimate_plan_cost(
    days: Sequence[Dict[str, Any]],
    prices: Sequence[Dict[str, Any]],
    monthly_budget: Optional[float] = None,
    swaps: Optional[Dict[str, Dict[str, Any]]] = None,
    budget: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Estimate one serving per listed plan item."""

    day_rows = []
    missing_counter = Counter()
    partial_counter = Counter()
    item_totals = {}
    used_dates = []
    matched_count = 0
    total_item_count = 0
    total_cost = 0.0
    priced_any = False

    for index, day in enumerate(days or [], start=1):
        meal_rows = []
        day_cost = 0.0
        day_complete = True
        day_priced = False

        for meal in (day or {}).get("meals", []) or []:
            meal_cost = 0.0
            meal_complete = True
            meal_priced = False
            priced_items = []

            for raw_name in (meal or {}).get("items", []) or []:
                name = str(raw_name).strip()
                if not name:
                    continue

                total_item_count += 1
                price = price_plan_item(name, prices or [], swaps)

                item = item_totals.setdefault(name, {"name": name, "times": 0, "total": 0.0})
                item["times"] += 1

                if price["cost"] is None:
                    missing_counter[name] += 1
                    meal_complete = False
                    continue

                if price["complete"]:
                    matched_count += 1
                else:
                    partial_counter[name] += 1
                    meal_complete = False

                meal_priced = True
                meal_cost += price["cost"]
                item["total"] += price["cost"]

                price_date = _date_value(price["price_date"])
                if price_date is not None:
                    used_dates.append(price_date)

                priced_items.append({
                    "name": name,
                    "price_bdt": _money(price["cost"]),
                    "complete": price["complete"],
                })

            day_cost += meal_cost
            day_complete = day_complete and meal_complete
            day_priced = day_priced or meal_priced
            meal_rows.append({
                "meal_type": (meal or {}).get("meal_type", "Meal"),
                "time": (meal or {}).get("time"),
                "items": priced_items,
                # None = nothing in this meal has a recorded price (never shown as 0).
                "cost_bdt": _money(meal_cost) if meal_priced else None,
                "complete": meal_complete,
            })

        total_cost += day_cost
        priced_any = priced_any or day_priced
        day_rows.append({
            "day": (day or {}).get("day", index),
            "label": (day or {}).get("label"),
            "meals": meal_rows,
            "cost_bdt": _money(day_cost) if day_priced else None,
            "complete": day_complete,
        })

    day_count = len(days or [])
    average_day = total_cost / day_count if day_count else 0
    per_week = average_day * 7
    per_month = average_day * 30
    complete = total_item_count > 0 and matched_count == total_item_count
    coverage = round(matched_count / total_item_count * 100) if total_item_count else 100

    top_items = sorted(
        (
            {"name": item["name"], "times": item["times"], "total_bdt": _money(item["total"])}
            for item in item_totals.values()
            if item["total"] > 0
        ),
        key=lambda item: item["total_bdt"],
        reverse=True,
    )[:5]

    warnings = []
    if not complete and priced_any:
        warnings.append("Some items have no price yet, so the real cost is higher than shown.")
    if partial_counter:
        warnings.append(
            "Some dishes are only partly priced (for example oats porridge: milk has a price, oats does not)."
        )
    if swaps:
        warnings.append("Your saved Smart Swaps are included in this estimate.")

    oldest_date = min(used_dates) if used_dates else None
    prices_as_of = oldest_date.strftime("%d %b %Y") if oldest_date else None
    if prices_as_of:
        warnings.append(f"Estimate based on prices recorded on or after {prices_as_of}.")

    return {
        "per_day_bdt": _money(average_day) if priced_any else None,
        "per_week_bdt": _money(per_week) if priced_any else None,
        "per_month_bdt": _money(per_month) if priced_any else None,
        "complete": complete,
        "priced_items": matched_count,
        "total_items": total_item_count,
        "days": day_rows,
        "budget": _budget_summary(per_month, complete, priced_any, monthly_budget, budget),
        "top_items": top_items,
        "missing_prices": [
            {"name": name, "occurrences": count}
            for name, count in sorted(missing_counter.items())
        ],
        "partly_priced": [
            {"name": name, "occurrences": count}
            for name, count in sorted(partial_counter.items())
        ],
        "coverage_percent": coverage,
        "prices_as_of": prices_as_of,
        "warnings": warnings,
        "disclaimer": (
            "This is an estimate based on available recorded prices. "
            "Actual costs may vary by market, shop, and serving size."
        ),
    }
