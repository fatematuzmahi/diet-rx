"""SCRUM-59: Budget-aware Bengali food optimizer + local alternatives
(SRS 3.3.3.2, 3.3.3.5).

Pure rules, no database, so everything here is unit-tested directly.

Order of decisions (SRS 3.3.3.2.3 / 3.3.3.5.4 -- safety before cost):

1. ``screen_option``      is this food safe for THIS patient?  (allergies,
                          diet type, conditions, prescriptions)
2. same food group        protein is only swapped for protein, grain for grain
3. recorded price         only foods with a FOOD_PRICE row (source + date)
4. cheaper                the swap must cost less for the same amount of food
5. explanation            every suggestion says why (3.3.3.5.5); every food
                          that was NOT suggested says why not.

Protein swaps keep roughly the same protein: 1 piece of fish is compared with
enough eggs / dal / chana to give a similar amount. The protein numbers below
are approximate typical values for the serving sizes used by the price list,
rounded, and are used only to compare foods -- they are not shown as exact
nutrition facts.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from backend.services.grocery_service import GROCERY_ITEMS, _norm, build_grocery_list
from backend.services.plan_conflict_resolver import resolve_effective_restrictions
from backend.services.plan_generator import _matches_allergen, allergen_terms
from backend.services.price_service import is_stale

PROTEIN, GRAIN, DAIRY, VEGETABLE, FRUIT, DRINK = (
    "protein", "grain", "dairy", "vegetable", "fruit", "drink")

ROLE_LABEL = {
    PROTEIN: "protein food", GRAIN: "grain / staple", DAIRY: "dairy",
    VEGETABLE: "vegetable", FRUIT: "fruit", DRINK: "drink",
}

MAX_ALTERNATIVES = 3

DISCLAIMER = (
    "Swaps are suggestions based on your saved health profile, prescriptions and recorded "
    "local prices. Prices are estimates. DietRx is a decision-support system and does not "
    "replace professional medical advice; ask your doctor or nutritionist before big diet changes."
)

# --------------------------------------------------------------------------- #
# Swappable foods (keys are the grocery keys used by SCRUM-60)
# --------------------------------------------------------------------------- #
#: key -> facts used by the rules.
#:   role        food group; swaps stay inside one group
#:   prices      FOOD_PRICE item names that price ONE serving of this food
#:   words       words used to match allergies and prescription text
#:   flags       meat_fish / egg / dairy / wheat / legume / sugary / salty / tea
#:   protein_g   approximate protein in one serving (protein group only)
#:   whole       servings must be whole numbers (eggs)
#:   local       why it is a familiar Bangladeshi choice
OPTIONS: Dict[str, Dict[str, Any]] = {
    "fish": {"role": PROTEIN, "prices": ("Fish",), "words": "fish rui",
             "flags": {"meat_fish"}, "protein_g": 17, "local": "Rui is one of the most eaten fish in Bangladesh."},
    "chicken": {"role": PROTEIN, "prices": ("Chicken",), "words": "chicken",
                "flags": {"meat_fish"}, "protein_g": 19, "local": "Broiler chicken is sold in every local bazar."},
    "eggs": {"role": PROTEIN, "prices": ("Egg",), "words": "egg", "flags": {"egg"},
             "protein_g": 6, "whole": True, "local": "Eggs are cheap, easy to cook and sold everywhere."},
    "dal": {"role": PROTEIN, "prices": ("Dal", "Lentil"), "words": "dal lentil masoor",
            "flags": {"legume"}, "protein_g": 7, "local": "Dal is part of almost every Bangladeshi meal."},
    "chana": {"role": PROTEIN, "prices": ("Chana",), "words": "chana chickpea",
              "flags": {"legume"}, "protein_g": 6, "local": "Chana is a common, filling Bangladeshi snack."},
    "sprouts": {"role": PROTEIN, "prices": ("Sprouts",), "words": "mung moong sprout bean",
                "flags": {"legume"}, "protein_g": 7, "local": "Mung beans are grown and eaten across Bangladesh."},
    "paneer": {"role": PROTEIN, "prices": ("Paneer",), "words": "paneer cheese",
               "flags": {"dairy"}, "protein_g": 9, "local": "Paneer is available in larger bazars."},
    "rice": {"role": GRAIN, "prices": ("Rice",), "words": "rice", "flags": set(),
             "local": "Rice is the main staple in Bangladesh."},
    "atta": {"role": GRAIN, "prices": ("Roti",), "words": "atta roti wheat", "flags": {"wheat"},
             "local": "Whole-wheat roti is a common Bangladeshi breakfast."},
    "bread": {"role": GRAIN, "prices": ("Whole wheat bread", "Bread"), "words": "bread wheat toast",
              "flags": {"wheat"}, "local": "Bread is sold in every corner shop."},
    "oats": {"role": GRAIN, "prices": ("Oats",), "words": "oats", "flags": set(),
             "local": "Oats are sold in most supermarkets."},
    "milk": {"role": DAIRY, "prices": ("Milk",), "words": "milk", "flags": {"dairy"},
             "local": "Milk is delivered and sold in every area."},
    "curd": {"role": DAIRY, "prices": ("Curd", "Doi"), "words": "curd doi yogurt", "flags": {"dairy"},
             "local": "Doi is a traditional Bangladeshi food."},
    "buttermilk": {"role": DAIRY, "prices": ("Buttermilk",), "words": "buttermilk", "flags": {"dairy"},
                   "local": "Ghol / buttermilk is a traditional drink."},
    "vegetables": {"role": VEGETABLE, "prices": ("Vegetables",), "words": "vegetable",
                   "flags": set(), "local": "Seasonal vegetables are the cheapest in the local bazar."},
    "salad": {"role": VEGETABLE, "prices": ("Salad",), "words": "salad cucumber tomato",
              "flags": set(), "local": "Cucumber salad is a common side dish."},
    "fruit": {"role": FRUIT, "prices": ("Seasonal fruit",), "words": "fruit papaya",
              "flags": {"sugary"}, "local": "Local seasonal fruit costs less than imported fruit."},
    "banana": {"role": FRUIT, "prices": ("Banana",), "words": "banana", "flags": {"sugary"},
               "local": "Bananas are grown across Bangladesh."},
    "apple": {"role": FRUIT, "prices": ("Apple",), "words": "apple", "flags": {"sugary"},
              "local": "Apples are mostly imported."},
    "lemon": {"role": DRINK, "prices": ("Lime water",), "words": "lemon lime", "flags": set(),
              "local": "Lebu pani is a common home drink."},
    "coconut_water": {"role": DRINK, "prices": ("Coconut water",), "words": "coconut",
                      "flags": {"sugary"}, "local": "Daab is sold on many streets."},
    "green_tea": {"role": DRINK, "prices": ("Green tea",), "words": "tea", "flags": {"tea"},
                  "local": "Tea is widely drunk in Bangladesh."},
}

SEASON_TEXT = {
    "ALL_YEAR": "Available all year",
    "VARIES": "Price changes with the season",
    "WINTER": "Best in winter",
    "SUMMER": "Best in summer",
    "MONSOON": "Best in the monsoon",
}

#: Whole grains are only swapped for other whole grains (keeps the fibre).
WHOLE_GRAINS = ("atta", "bread", "oats")
REFINED_GRAINS = ("rice",)

_KIDNEY_WORDS = ("kidney", "renal", "ckd", "dialysis")
_NON_VEG_PREF = re.compile(r"non[\s-]*veg")


# --------------------------------------------------------------------------- #
# Patient context (who is this for?)
# --------------------------------------------------------------------------- #
def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple, set)):
        return ", ".join(str(v) for v in value)
    return str(value)


def build_context(profile: Optional[Dict[str, Any]] = None,
                  prescriptions: Optional[Sequence[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Everything the safety screen needs, from the saved profile + prescriptions."""
    profile = profile or {}
    prescriptions = list(prescriptions or [])
    allergies = _text(profile.get("allergies"))
    conditions = _text(profile.get("conditions"))
    preference = _text(profile.get("food_preference"))
    rx_text = " ".join(_text(p.get("restrictions")) for p in prescriptions if isinstance(p, dict))

    tokens = set(resolve_effective_restrictions(prescriptions, conditions, allergies))
    # Food preference is free text such as "Vegetarian", "Non-vegetarian", "Vegan".
    pref = preference.lower()
    if "vegan" in pref:
        tokens.add("vegan")
    if "vegetarian" in pref and not _NON_VEG_PREF.search(pref):
        tokens.add("vegetarian")
    for word in ("gluten", "dairy free", "dairy-free", "lactose"):
        if word in pref:
            tokens.add("gluten_free" if word == "gluten" else "lactose_free")

    return {
        "allergy_terms": allergen_terms(allergies),
        "tokens": tokens,
        "kidney": any(w in conditions.lower() for w in _KIDNEY_WORDS) or "low_protein" in tokens,
        "anaemia": any(w in conditions.lower() for w in ("anemi", "anaemi", "low iron", "iron deficien")),
        "rx_text": rx_text.lower(),
        "checked_against": {
            "allergies": allergies or None,
            "conditions": conditions or None,
            "food_preference": preference or None,
            "prescriptions": len(prescriptions),
        },
    }


_LIMIT_WORDS = re.compile(r"\b(avoid|no|not|never|limit|restrict|reduce|less|without|stop|allerg\w*|intoleran\w*)\b")


def _prescription_limits(words: str, rx_text: str) -> bool:
    """True when a prescription clause both names this food and limits it
    ("avoid eggs", "limit dal"). "Eat more vegetables" does not count."""
    if not rx_text:
        return False
    for clause in re.split(r"[.;\n]", rx_text):
        if not _LIMIT_WORDS.search(clause):
            continue
        if any(re.search(rf"\b{re.escape(w)}", clause) for w in words.split() if len(w) > 2):
            return True
    return False


def screen_option(key: str, ctx: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    """(True, None) when the food is safe for this patient, else (False, why not)."""
    opt = OPTIONS.get(key)
    if opt is None:
        return False, "This food is not in the swap list yet."
    flags, tokens = opt["flags"], ctx["tokens"]
    name = GROCERY_ITEMS.get(key, (key,))[0]

    if ctx["allergy_terms"] and _matches_allergen(f"{name} {opt['words']}", ctx["allergy_terms"]):
        return False, "It is in your allergy list."
    if _prescription_limits(opt["words"], ctx["rx_text"]):
        return False, "Your prescription limits this food, so it is left for your doctor to decide."
    if "vegan" in tokens and flags & {"meat_fish", "egg", "dairy"}:
        return False, "Your diet is vegan."
    if "vegetarian" in tokens and flags & {"meat_fish", "egg"}:
        return False, "Your diet is vegetarian."
    if "gluten_free" in tokens and "wheat" in flags:
        return False, "It has wheat (gluten) and you follow a gluten-free diet."
    if "lactose_free" in tokens and "dairy" in flags:
        return False, "It is dairy and you follow a dairy-free / lactose-free diet."
    if ctx["kidney"] and "legume" in flags:
        return False, "Pulses are high in potassium and phosphorus; with a kidney condition ask your doctor first."
    if "low_sugar" in tokens and "sugary" in flags:
        return False, "It is high in natural sugar, which your diabetes / low-sugar rule limits."
    if ctx["anaemia"] and "tea" in flags:
        return False, "Tea lowers iron absorption, which matters for anaemia."
    return True, None


# --------------------------------------------------------------------------- #
# Prices
# --------------------------------------------------------------------------- #
def option_price(key: str, prices: Sequence[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The recorded price row for ONE serving of this food, or None."""
    opt = OPTIONS.get(key)
    if not opt:
        return None
    wanted = [_norm(n) for n in opt["prices"]]
    for name in wanted:
        for row in prices:
            if row.get("price_bdt") is not None and _norm(row.get("item_name", "")) == name:
                return row
    return None


def _ratio(original: str, alt: str) -> float:
    """Servings of ``alt`` that replace ONE serving of ``original``."""
    a, b = OPTIONS[original], OPTIONS[alt]
    if a["role"] != PROTEIN or not a.get("protein_g") or not b.get("protein_g"):
        return 1.0
    raw = a["protein_g"] / b["protein_g"]
    ratio = round(raw) if b.get("whole") else round(raw * 2) / 2
    return float(max(1, ratio))


def short_name(key: str) -> str:
    """"Fish (rui / local)" -> "fish", for use inside a sentence."""
    return GROCERY_ITEMS[key][0].split(" (")[0].lower()


def fmt_servings(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:.1f}"


def _money(value: Optional[float]) -> Optional[float]:
    return None if value is None else round(float(value), 2)


# --------------------------------------------------------------------------- #
# Alternatives for one food
# --------------------------------------------------------------------------- #
def alternatives_for(key: str, ctx: Dict[str, Any], prices: Sequence[Dict[str, Any]],
                     servings_per_week: Optional[float] = None,
                     limit: Optional[int] = MAX_ALTERNATIVES) -> Dict[str, Any]:
    """Safe, same-group, priced, cheaper alternatives for one food, with reasons.

    ``limit`` keeps the page short (best savings first); ``None`` returns all."""
    if key not in OPTIONS:
        raise KeyError(key)
    opt = OPTIONS[key]
    name = GROCERY_ITEMS[key][0]
    own = option_price(key, prices)
    own_price = float(own["price_bdt"]) if own else None
    suitable, unsuitable_reason = screen_option(key, ctx)

    alternatives: List[Dict[str, Any]] = []
    not_suggested: List[Dict[str, Any]] = []
    for alt in OPTIONS:
        if alt == key or OPTIONS[alt]["role"] != opt["role"]:
            continue
        alt_name = GROCERY_ITEMS[alt][0]
        ok, why_not = screen_option(alt, ctx)
        if not ok:
            not_suggested.append({"key": alt, "name": alt_name, "reason": why_not})
            continue
        row = option_price(alt, prices)
        if row is None:
            not_suggested.append({"key": alt, "name": alt_name,
                                  "reason": "No local price is recorded for it yet."})
            continue
        if key in WHOLE_GRAINS and alt in REFINED_GRAINS:
            not_suggested.append({"key": alt, "name": alt_name, "reason":
                                  "White rice has less fibre than whole grains, so it is not a like-for-like swap."})
            continue
        ratio = _ratio(key, alt)
        cost = float(row["price_bdt"]) * ratio
        saving = None if own_price is None else own_price - cost
        if suitable and saving is None:
            continue  # no price for the original: "cheaper" cannot be shown, so nothing is suggested
        if suitable and saving <= 0.005:
            not_suggested.append({"key": alt, "name": alt_name,
                                  "reason": "It is not cheaper for the same amount of food."})
            continue
        alternatives.append(_describe(key, alt, row, ratio, cost, saving, servings_per_week, suitable))

    alternatives.sort(key=lambda a: -(a["saving_per_week"] or a["saving_per_serving"] or 0))
    if limit:
        alternatives = alternatives[:limit]

    message = None
    if not alternatives:
        if own_price is None and suitable:
            message = (f"No local price is recorded for {short_name(key)} yet, so DietRx cannot tell "
                       "which food would be cheaper. No swap is suggested.")
        else:
            message = (f"No cheaper food that is safe for you was found for {short_name(key)}. "
                       "Keep it, or ask your nutritionist for other options.")

    return {
        "key": key,
        "name": name,
        "role": opt["role"],
        "role_label": ROLE_LABEL[opt["role"]],
        "unit": own.get("unit") if own else None,
        "price_per_serving": _money(own_price),
        "servings_per_week": None if servings_per_week is None else round(servings_per_week, 1),
        "cost_per_week": None if own_price is None or servings_per_week is None
        else round(own_price * servings_per_week, 2),
        "suitable": suitable,
        "unsuitable_reason": unsuitable_reason,
        "alternatives": alternatives,
        "not_suggested": not_suggested,
        "message": message,
    }


def _describe(key, alt, row, ratio, cost, saving, servings_per_week, original_suitable) -> Dict[str, Any]:
    opt, alt_opt = OPTIONS[key], OPTIONS[alt]
    name, alt_name = short_name(key), GROCERY_ITEMS[alt][0]
    unit = row.get("unit") or "1 serving"
    portion = unit if ratio == 1 else f"{fmt_servings(ratio)} × {unit}"
    per_week = None if saving is None or servings_per_week is None else saving * servings_per_week

    why: List[str] = []
    if not original_suitable:
        why.append(f"{name.capitalize()} does not suit your saved health information; this is a safe swap.")
    if saving is not None and saving > 0:
        why.append(f"Saves ৳{saving:,.2f} for each {name} serving"
                   + (f" (about ৳{per_week:,.0f} a week)." if per_week else "."))
    if opt["role"] == PROTEIN and ratio != 1:
        why.append(f"{portion} gives about the same protein as one serving of {name}.")
    else:
        why.append(f"Same food group ({ROLE_LABEL[opt['role']]}), so your plan stays balanced.")
    if "legume" in alt_opt["flags"]:
        why.append("Plant protein: eat it with rice or roti for a complete protein.")
    why.append(alt_opt["local"])
    season = (row.get("season") or "ALL_YEAR").upper()
    why.append(SEASON_TEXT.get(season, "Availability changes with the season") + ".")
    why.append("Checked against your allergies, diet, conditions and prescriptions.")
    stale = bool(row.get("stale")) if "stale" in row else is_stale(row.get("price_date"))
    if stale:
        why.append("This price may be outdated; check your local bazar.")

    return {
        "key": alt,
        "name": alt_name,
        "servings_ratio": ratio,
        "portion": portion,
        "price_per_serving": _money(row["price_bdt"]),
        "cost_per_original_serving": _money(cost),
        "saving_per_serving": _money(saving),
        "saving_per_week": None if per_week is None else round(per_week, 2),
        "why": why,
        "season": season,
        "season_note": row.get("season_note"),
        "source": row.get("source"),
        "source_url": row.get("source_url"),
        "price_date": row.get("price_date"),
        "stale": stale,
    }


# --------------------------------------------------------------------------- #
# Saved swaps
# --------------------------------------------------------------------------- #
def validate_swap(from_key: str, to_key: str, ctx: Dict[str, Any],
                  prices: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Return the alternative when it is still allowed, else raise ValueError(why)."""
    if from_key not in OPTIONS or to_key not in OPTIONS:
        raise ValueError("That food cannot be swapped.")
    if from_key == to_key:
        raise ValueError("Choose a different food to swap to.")
    result = alternatives_for(from_key, ctx, prices, limit=None)
    for alt in result["alternatives"]:
        if alt["key"] == to_key:
            return alt
    for item in result["not_suggested"]:
        if item["key"] == to_key:
            raise ValueError(f"{item['name']} cannot be used: {item['reason']}")
    raise ValueError(f"{GROCERY_ITEMS[to_key][0]} is not a suitable swap for {GROCERY_ITEMS[from_key][0]}.")


def grocery_swaps(rows: Iterable[Dict[str, Any]], ctx: Dict[str, Any],
                  prices: Sequence[Dict[str, Any]]) -> Tuple[Dict[str, Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Saved swaps -> (map for build_grocery_list, active list, paused list).

    Each saved swap is checked again, because the profile or prices may have
    changed since it was saved. A swap that is no longer safe is paused, never
    applied.
    """
    swap_map: Dict[str, Dict[str, Any]] = {}
    active: List[Dict[str, Any]] = []
    paused: List[Dict[str, Any]] = []
    for row in rows or []:
        from_key, to_key = row.get("from_item"), row.get("to_item")
        base = {"swap_id": row.get("swap_id"), "from_key": from_key, "to_key": to_key,
                "from_name": GROCERY_ITEMS.get(from_key, (from_key,))[0],
                "to_name": GROCERY_ITEMS.get(to_key, (to_key,))[0],
                "created_at": row.get("created_at")}
        try:
            alt = validate_swap(from_key, to_key, ctx, prices)
        except ValueError as error:
            paused.append({**base, "reason": str(error)})
            continue
        swap_map[from_key] = {"to_key": to_key, "ratio": alt["servings_ratio"],
                              "price_row": option_price(to_key, prices), "from_name": base["from_name"]}
        active.append({**base, "portion": alt["portion"], "saving_per_serving": alt["saving_per_serving"]})
    return swap_map, active, paused


# --------------------------------------------------------------------------- #
# Whole plan
# --------------------------------------------------------------------------- #
def build_summary(days: Sequence[Dict[str, Any]], prices: Sequence[Dict[str, Any]],
                  ctx: Dict[str, Any], budget: Optional[Dict[str, Any]] = None,
                  swap_rows: Optional[Iterable[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Plan cost vs budget, the best savings ideas, and the saved swaps."""
    swap_map, active, paused = grocery_swaps(swap_rows or [], ctx, prices)
    grocery = build_grocery_list(days, prices, None, swaps=swap_map)
    original = build_grocery_list(days, prices, None) if swap_map else grocery
    plan_days = grocery["plan_days"]
    to_week = 7 / plan_days

    def weekly(g):
        total = g["summary"]["total_bdt"]
        return None if total is None else round(total * to_week)

    ideas: List[Dict[str, Any]] = []
    for category in grocery["categories"]:
        for line in category["items"]:
            if line["key"] not in OPTIONS or not line["servings"]:
                continue
            idea = alternatives_for(line["key"], ctx, prices, servings_per_week=line["servings"] * to_week)
            if idea["alternatives"]:
                idea["used_in"] = line["used_in"]
                ideas.append(idea)
    ideas.sort(key=lambda i: (i["suitable"], -max((a["saving_per_week"] or 0) for a in i["alternatives"])))

    cost_week = weekly(grocery)
    budget = budget or {}
    budget_week = budget.get("per_week") if budget.get("found") else None
    if budget_week is None:
        status = "not_set"
    elif cost_week is None:
        status = "unknown"
    elif cost_week > budget_week:
        status = "above"
    else:
        status = "within" if grocery["summary"]["complete"] else "within_so_far"
    possible = sum(max((a["saving_per_week"] or 0) for a in i["alternatives"]) for i in ideas)

    return {
        "plan_days": plan_days,
        "cost_per_week": cost_week,
        "cost_per_week_before_swaps": weekly(original),
        "saved_per_week_by_swaps": None if weekly(original) is None or cost_week is None
        else max(0, weekly(original) - cost_week),
        "budget_per_week": budget_week,
        "budget_mode": budget.get("mode") if budget.get("found") else None,
        "status": status,
        "over_budget_by": round(cost_week - budget_week) if status == "above" else None,
        "possible_saving_per_week": round(possible),
        "price_coverage_percent": grocery["summary"]["coverage_percent"],
        "prices_complete": grocery["summary"]["complete"],
        "ideas": ideas,
        "active_swaps": active,
        "paused_swaps": paused,
        "checked_against": ctx["checked_against"],
        "notes": grocery["notes"],
        "disclaimer": DISCLAIMER,
    }


def food_choices() -> List[Dict[str, str]]:
    """Every food the optimizer knows, for the page's "check any food" list."""
    return [{"key": k, "name": GROCERY_ITEMS[k][0], "role": o["role"], "role_label": ROLE_LABEL[o["role"]]}
            for k, o in OPTIONS.items()]


def resolve_key(item: str) -> Optional[str]:
    """A grocery key or a display / dish name -> grocery key."""
    text = (item or "").strip().lower()
    if not text:
        return None
    if text in OPTIONS:
        return text
    for key in OPTIONS:
        if _norm(GROCERY_ITEMS[key][0]) == _norm(text) or key.replace("_", " ") == text:
            return key
    from backend.services.grocery_service import PLAN_ITEM_MAP
    keys = PLAN_ITEM_MAP.get(text)
    if keys:
        for key in keys:
            if key in OPTIONS:
                return key
    for key, opt in OPTIONS.items():
        if any(_norm(w) in _norm(text).split() for w in opt["words"].split()):
            return key
    return None
