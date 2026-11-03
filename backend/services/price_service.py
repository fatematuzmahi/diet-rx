"""SCRUM-57: local price data + user budget (SRS 3.3.3.1).

Pure helpers (no database), so the rules can be unit-tested:

* ``SEED_PRICES``      the seeded Bangladeshi items. Every row keeps the
                       market price it was calculated from, the serving size,
                       the source article and its date (prices are
                       time-sensitive estimates).
* ``serving_price``    market price x serving share -> price of one serving.
* ``normalise_budget`` a DAILY / WEEKLY / MONTHLY / PLAN budget -> the same
                       budget per day, per week and per month.
* ``is_stale``         True when a price is older than ``STALE_AFTER_DAYS``.

``price_bdt`` is always the price of ONE serving, so the grocery list
(SCRUM-60) and the cost estimate (SCRUM-58) can add servings directly.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, List, Optional

STALE_AFTER_DAYS = 30
DAYS_PER_MONTH = 30
BUDGET_MODES = ("DAILY", "WEEKLY", "MONTHLY", "PLAN")

DISCLAIMER = (
    "Prices are estimates from published Dhaka retail market reports on the date shown. "
    "Real prices change by market, shop and season."
)

# --------------------------------------------------------------------------- #
# Sources (each seeded row points to one of these)
# --------------------------------------------------------------------------- #
SRC_TBS_MARKET = (
    "The Business Standard, Dhaka kitchen market report",
    "https://www.tbsnews.net/markets/dhaka-markets-see-relief-vegetable-fish-prices-chicken-stays-costly-1580531",
    date(2026, 10, 22),
)
SRC_TBS_RICE = (
    "The Business Standard, Dhaka rice prices (TCB data)",
    "https://www.tbsnews.net/bangladesh/rice-prices-rise-tk3-kg-dhaka-paddy-transport-costs-increase-1567216",
    date(2026, 10, 9),
)
SRC_MILK = (
    "Daily Observer, Milk Vita retail price",
    "https://observerbd.com/news/587450",
    date(2026, 8, 11),
)

VEG_SEASON_NOTE = (
    "Vegetable prices change with the season. In late October new winter vegetables "
    "were arriving and prices were falling."
)

# item_name, category, serving description, serving share of the market unit,
# market item, market unit, low, high, source, season, season note
_SEED = [
    ("Rice", "Grains", "1 plate (75 g raw)", 0.075, "Coarse rice", "kg", 50, 55, SRC_TBS_RICE, "ALL_YEAR", None),
    ("Roti", "Grains", "2 pieces (60 g whole-wheat flour)", 0.060, "Whole-wheat flour (2 kg pack Tk140)", "kg", 70, 70, SRC_TBS_MARKET, "ALL_YEAR", None),
    ("Dal", "Pulses", "1 bowl (30 g raw lentils)", 0.030, "Coarse lentils", "kg", 90, 90, SRC_TBS_MARKET, "ALL_YEAR", None),
    ("Lentil", "Pulses", "1 bowl (30 g raw lentils)", 0.030, "Coarse lentils", "kg", 90, 90, SRC_TBS_MARKET, "ALL_YEAR", None),
    ("Chana", "Pulses", "1 small bowl (30 g chickpeas)", 0.030, "Chickpeas", "kg", 120, 120, SRC_TBS_MARKET, "ALL_YEAR", None),
    ("Sprouts", "Pulses", "1 small bowl (30 g mung beans)", 0.030, "Large mung beans", "kg", 140, 140, SRC_TBS_MARKET, "ALL_YEAR", None),
    ("Egg", "Protein", "1 egg", 1 / 12, "Farm eggs", "dozen", 150, 150, SRC_TBS_MARKET, "ALL_YEAR", None),
    ("Chicken", "Protein", "1 portion (100 g raw)", 0.100, "Broiler chicken", "kg", 180, 180, SRC_TBS_MARKET, "ALL_YEAR", None),
    ("Fish", "Protein", "1 piece (100 g raw rui)", 0.100, "Large rui fish", "kg", 350, 360, SRC_TBS_MARKET, "ALL_YEAR", None),
    ("Milk", "Dairy", "1 glass (200 ml)", 0.200, "Milk Vita pasteurised milk", "litre", 110, 110, SRC_MILK, "ALL_YEAR", None),
    ("Vegetables", "Vegetables", "1 bowl (100 g)", 0.100, "Pointed gourd / okra / cauliflower", "kg", 60, 60, SRC_TBS_MARKET, "VARIES", VEG_SEASON_NOTE),
    ("Salad", "Vegetables", "1 bowl (100 g cucumber)", 0.100, "Cucumber", "kg", 70, 80, SRC_TBS_MARKET, "VARIES", VEG_SEASON_NOTE),
    ("Lime water", "Drinks", "1 glass (1 lemon)", 0.250, "Lemons (Tk60 for four)", "4 pieces", 60, 60, SRC_TBS_MARKET, "ALL_YEAR", None),
    ("Seasonal fruit", "Fruits", "1 bowl (150 g papaya)", 0.150, "Papaya", "kg", 30, 30, SRC_TBS_MARKET, "VARIES", "Fruit varies by season; papaya is used as the reference."),
]


def serving_price(low: float, high: float, share: float) -> float:
    """Price of one serving: the midpoint of the market range x the serving share."""
    if low is None or high is None or share is None or share <= 0:
        raise ValueError("A serving price needs a market price and a positive serving share.")
    return round(((float(low) + float(high)) / 2) * float(share), 2)


def seed_rows() -> List[Dict[str, Any]]:
    rows = []
    for (name, category, unit, share, m_item, m_unit, low, high, src, season, note) in _SEED:
        rows.append({
            "item_name": name, "category": category, "unit": unit,
            "price_bdt": serving_price(low, high, share),
            "market_item": m_item, "market_unit": m_unit,
            "market_price_low": float(low), "market_price_high": float(high),
            "serving_share": round(float(share), 4),
            "market": "Dhaka retail markets",
            "source": src[0], "source_url": src[1], "price_date": src[2],
            "season": season, "season_note": note,
        })
    return rows


SEED_PRICES = seed_rows()


def is_stale(price_date: Any, today: Optional[date] = None) -> bool:
    if price_date is None:
        return True
    if isinstance(price_date, datetime):
        price_date = price_date.date()
    today = today or date.today()
    return (today - price_date).days > STALE_AFTER_DAYS


def normalise_budget(mode: str, amount: float, plan_days: Optional[int] = None) -> Dict[str, Any]:
    """Turn any budget mode into the same money per day / week / month."""
    mode = (mode or "").upper()
    amount = float(amount)
    if mode not in BUDGET_MODES:
        raise ValueError("Budget type must be daily, weekly, monthly or plan.")
    if amount <= 0:
        raise ValueError("Budget must be more than 0.")
    if mode == "DAILY":
        per_day = amount
    elif mode == "WEEKLY":
        per_day = amount / 7
    elif mode == "MONTHLY":
        per_day = amount / DAYS_PER_MONTH
    else:
        if not plan_days or plan_days < 1:
            raise ValueError("A plan budget needs the number of plan days.")
        per_day = amount / plan_days
    return {
        "per_day": round(per_day, 2),
        "per_week": round(per_day * 7, 2),
        "per_month": round(per_day * DAYS_PER_MONTH, 2),
    }


def row_to_price(row, today: Optional[date] = None) -> Dict[str, Any]:
    (pid, name, category, unit, price, m_item, m_unit, low, high, share,
     market, source, url, p_date, season, note, updated_at) = row
    if isinstance(p_date, datetime):
        p_date = p_date.date()
    return {
        "price_id": int(pid), "item_name": name, "category": category, "unit": unit,
        "price_bdt": float(price) if price is not None else None,
        "market_item": m_item, "market_unit": m_unit,
        "market_price_low": float(low) if low is not None else None,
        "market_price_high": float(high) if high is not None else None,
        "serving_share": float(share) if share is not None else None,
        "market": market, "source": source, "source_url": url,
        "price_date": p_date, "season": season, "season_note": note,
        "updated_at": updated_at, "stale": is_stale(p_date, today),
    }
