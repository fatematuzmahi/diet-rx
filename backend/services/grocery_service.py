"""SCRUM-60: Grocery list generation + purchase links (SRS 3.3.3.3, 3.3.3.4).

Pure functions, no database access, so everything here can be unit-tested:

    build_grocery_list(days, prices, monthly_budget) -> dict
    purchase_links(search_term) -> list of validated outbound links

Rules
-----
* The list is built from the ACCEPTED meal plan's days. Every time a plan item
  appears it counts as one serving; servings of the same grocery item are
  added together (quantity aggregation).
* Cost comes only from DietRx's own FOOD_PRICE data (SCRUM-57). An item
  without a recorded price is shown as "price not available" and is NOT
  guessed. Totals say how many items they cover.
* Purchase links go only to allow-listed HTTPS shops and carry ONLY a generic
  grocery search word taken from the fixed table below. Nothing typed by the
  user, no patient id and no health condition can ever be sent
  (data minimisation). External prices are never shown or guaranteed.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import quote, urlsplit

# --------------------------------------------------------------------------- #
# Plan item -> grocery item(s)
# --------------------------------------------------------------------------- #
#: grocery key -> (display name, category, shop search word or None)
#: A search word of None means "buy fresh at the local market": no link.
GROCERY_ITEMS: Dict[str, Tuple[str, str, Optional[str]]] = {
    "rice": ("Rice", "Grains & bread", "rice"),
    "atta": ("Atta (whole wheat flour)", "Grains & bread", "atta"),
    "bread": ("Whole wheat bread", "Grains & bread", "brown bread"),
    "oats": ("Oats", "Grains & bread", "oats"),
    "biscuit": ("Whole wheat biscuit", "Grains & bread", "wheat biscuit"),
    "dal": ("Lentils (dal)", "Pulses & protein", "masoor dal"),
    "chana": ("Chickpeas (chana)", "Pulses & protein", "chickpeas"),
    "sprouts": ("Moong beans for sprouts", "Pulses & protein", "moong dal"),
    "eggs": ("Eggs", "Pulses & protein", "eggs"),
    "fish": ("Fish (rui / local)", "Pulses & protein", "rui fish"),
    "chicken": ("Chicken", "Pulses & protein", "chicken"),
    "milk": ("Milk", "Dairy", "milk"),
    "curd": ("Curd (doi)", "Dairy", "doi"),
    "buttermilk": ("Buttermilk", "Dairy", "buttermilk"),
    "paneer": ("Paneer", "Dairy", "paneer"),
    "vegetables": ("Seasonal vegetables", "Vegetables", None),
    "salad": ("Salad vegetables (cucumber, tomato)", "Vegetables", None),
    "lemon": ("Lemon", "Vegetables", "lemon"),
    "banana": ("Banana", "Fruits", "banana"),
    "apple": ("Apple", "Fruits", "apple"),
    "fruit": ("Seasonal fruit", "Fruits", None),
    "coconut_water": ("Coconut water", "Drinks & snacks", "coconut water"),
    "green_tea": ("Green tea", "Drinks & snacks", "green tea"),
    "nuts": ("Mixed nuts", "Drinks & snacks", "mixed nuts"),
    "peanut_chikki": ("Peanut chikki", "Drinks & snacks", "peanut chikki"),
    "papad": ("Papad", "Drinks & snacks", "papad"),
}

#: Meal-plan item (lower case) -> grocery keys it needs.
PLAN_ITEM_MAP: Dict[str, Tuple[str, ...]] = {
    "rice": ("rice",),
    "plain rice": ("rice",),
    "khichuri": ("rice", "dal"),
    "roti": ("atta",),
    "vegetable paratha": ("atta", "vegetables"),
    "whole wheat bread": ("bread",),
    "whole wheat toast": ("bread",),
    "whole wheat biscuit": ("biscuit",),
    "oats porridge with milk": ("oats", "milk"),
    "dal": ("dal",),
    "lentil soup": ("dal",),
    "lentil curry": ("dal",),
    "roasted chana": ("chana",),
    "sprouts": ("sprouts",),
    "sprouts chaat": ("sprouts",),
    "boiled egg": ("eggs",),
    "boiled eggs": ("eggs",),
    "egg curry": ("eggs",),
    "egg bhurji": ("eggs",),
    "grilled fish": ("fish",),
    "fish curry": ("fish",),
    "chicken curry": ("chicken",),
    "chicken stew": ("chicken",),
    "curd": ("curd",),
    "buttermilk": ("buttermilk",),
    "paneer curry": ("paneer",),
    "mixed vegetable curry": ("vegetables",),
    "mixed vegetables": ("vegetables",),
    "bengali dry vegetables": ("vegetables",),
    "bengali vegetables": ("vegetables",),
    "seasonal vegetables": ("vegetables",),
    "cooked vegetables": ("vegetables",),
    "cooked seasonal vegetables": ("vegetables",),
    "salad": ("salad",),
    "cucumber and tomato salad": ("salad",),
    "lime water": ("lemon",),
    "banana": ("banana",),
    "apple": ("apple",),
    "seasonal fruit": ("fruit",),
    "mixed fruit salad": ("fruit",),
    "coconut water": ("coconut_water",),
    "green tea": ("green_tea",),
    "roasted nuts": ("nuts",),
    "peanut chikki": ("peanut_chikki",),
    "papad": ("papad",),
}

CATEGORY_ORDER = (
    "Grains & bread", "Pulses & protein", "Dairy", "Vegetables", "Fruits",
    "Drinks & snacks", "Other",
)

# --------------------------------------------------------------------------- #
# Purchase links (SRS 3.3.3.4)
# --------------------------------------------------------------------------- #
#: Allow-listed shops. Only these hosts can ever be linked to.
STORES: Tuple[Dict[str, str], ...] = (
    {"id": "daraz", "name": "Daraz", "template": "https://www.daraz.com.bd/catalog/?q={q}"},
)
ALLOWED_HOSTS = frozenset({"www.daraz.com.bd"})
ALLOWED_SEARCH_WORDS = frozenset(w for _n, _c, w in GROCERY_ITEMS.values() if w)
_SAFE_WORD = re.compile(r"^[a-z ]{2,30}$")

DISCLAIMER = (
    "Costs are estimates from DietRx's recorded local prices, not shop prices. "
    "Shop links open an outside website: DietRx does not control or guarantee its "
    "prices or stock. Only the grocery item name is sent - never your name, "
    "account or health details."
)


def is_safe_search_word(word: Optional[str]) -> bool:
    """Data-minimisation check: only fixed, generic grocery words may leave DietRx."""
    return bool(word) and word in ALLOWED_SEARCH_WORDS and bool(_SAFE_WORD.match(word))


def _validated(url: str) -> bool:
    parts = urlsplit(url)
    return parts.scheme == "https" and parts.hostname in ALLOWED_HOSTS and not parts.username


def purchase_links(search_word: Optional[str]) -> Dict[str, Any]:
    """Validated outbound links for one grocery item, or a graceful reason."""
    if not search_word:
        return {"available": False, "reason": "Buy fresh at your local market.", "links": []}
    if not is_safe_search_word(search_word):
        return {"available": False, "reason": "No online link for this item.", "links": []}
    links = []
    for store in STORES:
        url = store["template"].format(q=quote(search_word))
        if _validated(url):
            links.append({"store": store["name"], "url": url})
    if not links:
        return {"available": False, "reason": "Online shops are not available right now.", "links": []}
    return {"available": True, "reason": None, "links": links}


# --------------------------------------------------------------------------- #
# Prices (from FOOD_PRICE, SCRUM-57)
# --------------------------------------------------------------------------- #
def _norm(text: str) -> str:
    words = re.sub(r"[^a-z ]", " ", (text or "").lower()).split()
    return " ".join(w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w for w in words)


def find_price(name: str, prices: Sequence[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Exact (singular) match first, else the longest price name inside ``name``."""
    target = _norm(name)
    if not target:
        return None
    best = None
    for row in prices:
        key = _norm(row.get("item_name", ""))
        if not key or row.get("price_bdt") is None:
            continue
        if key == target:
            return row
        if f" {key} " in f" {target} " and (best is None or len(key) > len(_norm(best["item_name"]))):
            best = row
    return best


# --------------------------------------------------------------------------- #
# The list
# --------------------------------------------------------------------------- #
def _plan_items(days: Iterable[Dict[str, Any]]) -> List[str]:
    items: List[str] = []
    for day in days or []:
        for meal in (day or {}).get("meals", []) or []:
            items.extend(str(i).strip() for i in (meal or {}).get("items", []) or [] if str(i).strip())
    return items


def build_grocery_list(
    days: Sequence[Dict[str, Any]],
    prices: Optional[Sequence[Dict[str, Any]]] = None,
    monthly_budget: Optional[float] = None,
) -> Dict[str, Any]:
    prices = list(prices or [])
    day_count = max(len(days or []), 1)
    groceries: Dict[str, Dict[str, Any]] = {}

    for plan_item in _plan_items(days):
        keys = PLAN_ITEM_MAP.get(plan_item.lower())
        if not keys:  # unknown item -> keep it as its own grocery line
            keys = ("other:" + plan_item.lower(),)
        price_row = find_price(plan_item, prices)
        for key in keys:
            if key.startswith("other:"):
                name, category, word = plan_item, "Other", None
            else:
                name, category, word = GROCERY_ITEMS[key]
            g = groceries.setdefault(key, {
                "key": key, "name": name, "category": category, "search_word": word,
                "servings": 0, "used_in": [], "cost_bdt": 0.0, "priced": 0,
                "covered": 0, "shared_with": [], "unit": None, "price_date": None,
            })
            g["servings"] += 1
            if plan_item not in g["used_in"]:
                g["used_in"].append(plan_item)
            if price_row is None:
                continue
            g["covered"] += 1
            if key == keys[0]:
                # A dish's price is counted once, on its first grocery line.
                g["cost_bdt"] += float(price_row["price_bdt"])
                g["priced"] += 1
                g["unit"] = g["unit"] or price_row.get("unit")
                d = price_row.get("price_date")
                if d is not None and (g["price_date"] is None or str(d) < str(g["price_date"])):
                    g["price_date"] = d
            elif plan_item not in g["shared_with"]:
                g["shared_with"].append(plan_item)

    items: List[Dict[str, Any]] = []
    dates = []
    for g in groceries.values():
        if g["covered"] == 0:
            status = "missing"
        elif g["covered"] < g["servings"]:
            status = "partial"
        else:
            status = "ok"
        if g["price_date"] is not None:
            dates.append(str(g["price_date"])[:10])
        cost_note = None
        if g["shared_with"] and not g["priced"]:
            cost_note = "Cost included with: " + ", ".join(g["shared_with"])
        items.append({
            "key": g["key"],
            "name": g["name"],
            "category": g["category"],
            "servings": g["servings"],
            "quantity_label": f"{g['servings']} serving{'s' if g['servings'] != 1 else ''}",
            "unit": g["unit"],
            "used_in": g["used_in"],
            "estimated_cost_bdt": round(g["cost_bdt"]) if g["priced"] else None,
            "cost_note": cost_note,
            "price_status": status,
            "purchase": purchase_links(g["search_word"]),
        })

    order = {c: i for i, c in enumerate(CATEGORY_ORDER)}
    items.sort(key=lambda i: (order.get(i["category"], 99), -i["servings"], i["name"]))

    priced = [i for i in items if i["estimated_cost_bdt"] is not None]
    total = float(sum(g["cost_bdt"] for g in groceries.values()))
    per_month = total / day_count * 30 if priced else None

    complete = bool(items) and all(i["price_status"] == "ok" for i in items)
    budget: Dict[str, Any] = {"monthly_budget": monthly_budget, "status": "not_set"}
    if monthly_budget:
        mb = float(monthly_budget)
        budget["budget_for_plan_days"] = round(mb * day_count / 30)
        if per_month is None:
            budget["status"] = "unknown"
        else:
            budget["difference_bdt"] = round(mb - per_month)
            budget["used_percent"] = round(per_month / mb * 100) if mb else None
            if per_month > mb:
                budget["status"] = "above"  # already above, even before missing prices
            else:
                # Missing prices can only add cost, so "within" is certain only when complete.
                budget["status"] = "within" if complete else "within_so_far"

    categories = [
        {"name": c, "items": [i for i in items if i["category"] == c]}
        for c in CATEGORY_ORDER if any(i["category"] == c for i in items)
    ]
    covered = [i for i in items if i["price_status"] == "ok"]
    coverage = round(len(covered) / len(items) * 100) if items else 0
    notes = []
    if not prices:
        notes.append("Price data is not added yet, so costs cannot be estimated.")
    elif coverage < 100:
        notes.append(f"{len(items) - len(covered)} item(s) have no full recorded price, so the real total is higher.")

    return {
        "plan_days": day_count,
        "item_count": len(items),
        "categories": categories,
        "summary": {
            "total_bdt": round(total) if priced else None,
            "per_day_bdt": round(total / day_count) if priced else None,
            "per_month_bdt": round(per_month) if per_month is not None else None,
            "priced_items": len(covered),
            "complete": complete,
            "coverage_percent": coverage,
            "prices_as_of": min(dates) if dates else None,
            "budget": budget,
        },
        "notes": notes,
        "disclaimer": DISCLAIMER,
    }
