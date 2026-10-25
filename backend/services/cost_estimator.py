
"""Pure cost calculations for SCRUM-58.

Prices must come from FOOD_PRICE; this module never invents prices.
"""

from collections import Counter
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Sequence
import re


def _norm(text: str) -> str:
    words = re.sub(r"[^a-z ]", " ", (text or "").lower()).split()
    return " ".join(
        word[:-1]
        if len(word) > 3 and word.endswith("s") and not word.endswith("ss")
        else word
        for word in words
    )


def _find_price(name: str, prices: Sequence[Dict[str, Any]]):
    target = _norm(name)
    if not target:
        return None

    matches = []
    for row in prices:
        price_name = _norm(str(row.get("item_name") or ""))
        if not price_name or row.get("price_bdt") is None:
            continue
        if price_name == target:
            return row
        if f" {price_name} " in f" {target} ":
            matches.append((len(price_name), row))

    return max(matches, key=lambda pair: pair[0])[1] if matches else None


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


def _money(value) -> int:
    return int(round(float(value or 0)))


def estimate_plan_cost(
    days: Sequence[Dict[str, Any]],
    prices: Sequence[Dict[str, Any]],
    monthly_budget: Optional[float] = None,
) -> Dict[str, Any]:
    """Estimate one serving per listed plan item."""

    day_rows = []
    all_item_names = []
    missing_counter = Counter()
    item_totals = {}
    used_dates = []
    matched_count = 0
    total_item_count = 0
    total_cost = 0

    for index, day in enumerate(days or [], start=1):
        meal_rows = []
        day_cost = 0

        for meal in (day or {}).get("meals", []) or []:
            meal_cost = 0
            priced_items = []

            for raw_name in (meal or {}).get("items", []) or []:
                name = str(raw_name).strip()
                if not name:
                    continue

                total_item_count += 1
                all_item_names.append(name)
                price_row = _find_price(name, prices or [])

                item = item_totals.setdefault(
                    name, {"name": name, "times": 0, "total": 0}
                )
                item["times"] += 1

                if price_row is None:
                    missing_counter[name] += 1
                    continue

                price = float(price_row["price_bdt"])
                meal_cost += price
                matched_count += 1
                item["total"] += price

                price_date = _date_value(price_row.get("price_date"))
                if price_date is not None:
                    used_dates.append(price_date)

                priced_items.append({
                    "name": name,
                    "price_bdt": _money(price),
                })

            day_cost += meal_cost
            meal_rows.append({
                "meal_type": (meal or {}).get("meal_type", "Meal"),
                "time": (meal or {}).get("time"),
                "items": priced_items,
                "cost_bdt": _money(meal_cost),
            })

        total_cost += day_cost
        day_rows.append({
            "day": (day or {}).get("day", index),
            "label": (day or {}).get("label"),
            "meals": meal_rows,
            "cost_bdt": _money(day_cost),
        })

    day_count = len(days or [])
    average_day = total_cost / day_count if day_count else 0
    per_week = average_day * 7
    per_month = average_day * 30
    coverage = (
        round(matched_count / total_item_count * 100)
        if total_item_count else 100
    )

    if monthly_budget is None:
        budget = {
            "monthly_budget_bdt": None,
            "status": "not_set",
            "difference_bdt": None,
            "used_percent": None,
            "budget_per_day_bdt": None,
        }
    else:
        difference = float(monthly_budget) - per_month
        budget = {
            "monthly_budget_bdt": _money(monthly_budget),
            "status": "within" if difference >= 0 else "above",
            "difference_bdt": _money(difference),
            "used_percent": (
                round(per_month / float(monthly_budget) * 100)
                if monthly_budget > 0 else None
            ),
            "budget_per_day_bdt": _money(float(monthly_budget) / 30),
        }

    top_items = sorted(
        (
            {
                "name": item["name"],
                "times": item["times"],
                "total_bdt": _money(item["total"]),
            }
            for item in item_totals.values()
            if item["total"] > 0
        ),
        key=lambda item: item["total_bdt"],
        reverse=True,
    )[:5]

    warnings = []
    if coverage < 80:
        warnings.append(
            "Some items have no price yet, so the real cost is higher than shown."
        )

    oldest_date = min(used_dates) if used_dates else None
    prices_as_of = (
        oldest_date.strftime("%d %b %Y") if oldest_date else None
    )
    if prices_as_of:
        warnings.append(
            f"Estimate based on prices recorded on or after {prices_as_of}."
        )

    return {
        "per_day_bdt": _money(average_day),
        "per_week_bdt": _money(per_week),
        "per_month_bdt": _money(per_month),
        "days": day_rows,
        "budget": budget,
        "top_items": top_items,
        "missing_prices": [
            {"name": name, "occurrences": count}
            for name, count in sorted(missing_counter.items())
        ],
        "coverage_percent": coverage,
        "prices_as_of": prices_as_of,
        "warnings": warnings,
        "disclaimer": (
            "This is an estimate based on available recorded prices. "
            "Actual costs may vary by market, shop, and serving size."
        ),
    }
