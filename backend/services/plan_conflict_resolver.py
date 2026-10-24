"""SCRUM-48: Multi-prescription / condition conflict resolver (SRS 3.3.4).

The starter implementation returned an empty list; this module implements the
real rules required by the SRS:

    3.3.4.1 accept dietary instructions from more than one prescription or
            condition profile;
    3.3.4.2 detect contradictory dietary rules, duplicate restrictions and
            potentially incompatible recommendations;
    3.3.4.3 explain the detected conflict in understandable language;
    3.3.4.4 produce only compatible recommendations when a safe intersection
            of rules can be determined;
    3.3.4.5 flag unresolved or high-risk conflicts for doctor/nutritionist
            review rather than silently choosing one rule.

Design
------
Restrictions are free text (a prescription note, an allergy list, a condition
name). They are normalised into a small set of *rule tokens* -- e.g. "low
sodium", "high protein", "diabetic" -- and the resolver then compares the
tokens coming from DIFFERENT sources. Two tokens that cannot both hold are
"contradictory"; the same token from two sources is a "duplicate".

Every conflict carries a human-readable ``explanation`` and, where a safe
intersection exists, a ``resolution`` that states which rule was kept and why
(the stricter/more protective one). Conflicts that cannot be safely
intersected are marked ``requires_review=True`` so the plan is flagged for a
doctor/nutritionist instead of silently picking a winner.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

# Severity levels used by the plan response.
SEVERITY_LOW = "low"
SEVERITY_MEDIUM = "medium"
SEVERITY_HIGH = "high"

#: Codes emitted by the resolver (stable identifiers for the frontend/tests).
CODE_DUPLICATE = "duplicate_restrictions"
CODE_CONTRADICTORY = "contradictory_restrictions"
CODE_COMPATIBLE = "compatible_restrictions"
CODE_GOAL_CONDITION = "goal_condition_conflict"
CODE_GOAL_PRESCRIPTION = "goal_prescription_conflict"
CODE_HIGH_RISK = "high_risk_restriction"

#: Normalised rule token -> the keywords that identify it in free text.
RULE_KEYWORDS: Dict[str, Tuple[str, ...]] = {
    "low_sodium": ("low sodium", "no salt", "salt free", "sodium free", "reduce salt", "less salt"),
    "high_sodium": ("high sodium", "more salt", "salty food", "increase salt"),
    "low_sugar": ("low sugar", "no sugar", "sugar free", "reduce sugar", "less sugar", "diabetic", "diabetes"),
    "high_sugar": ("high sugar", "more sugar", "increase sugar", "sugar rich"),
    "low_fat": ("low fat", "reduced fat", "less fat", "no fat"),
    "high_fat": ("high fat", "increase fat"),
    "low_carbs": ("low carb", "low carbohydrate", "no carb", "reduce carb", "ketogenic"),
    "high_carbs": ("high carb", "increase carb"),
    "low_protein": ("low protein", "restrict protein", "limit protein", "low protein diet"),
    "high_protein": ("high protein", "increase protein", "protein rich", "more protein"),
    "low_purine": ("low purine", "purine restricted", "gout"),
    "vegetarian": ("vegetarian", "no meat", "no fish"),
    "vegan": ("vegan", "no animal product"),
    "gluten_free": ("gluten free", "no gluten", "celiac", "coeliac"),
    "lactose_free": ("lactose free", "no dairy", "dairy free", "lactose intolerance"),
    "avoid_exercise": ("no exercise", "avoid exercise", "rest", "bed rest", "no physical activity", "do not exercise"),
    "increase_calories": ("increase calorie", "high calorie", "calorie dense", "weight gain", "gain weight"),
    "reduce_calories": ("reduce calorie", "low calorie", "calorie restriction", "weight loss"),
    "increase_fluid": ("increase fluid", "more water", "hydration"),
    "avoid_sugar_alcohol": ("no alcohol", "avoid alcohol"),
}

#: Pairs of tokens that CANNOT both be satisfied. The value is the safe
#: intersection: which rule to keep and why.
CONTRADICTORY_PAIRS: Dict[Tuple[str, str], Tuple[str, str]] = {
    ("low_sodium", "high_sodium"): (
        "low_sodium",
        "The stricter sodium limit was kept: high-sodium foods are excluded so "
        "the lower limit is still respected.",
    ),
    ("low_sugar", "high_sugar"): (
        "low_sugar",
        "The stricter sugar limit was kept: no added or high-sugar items are "
        "suggested.",
    ),
    ("low_fat", "high_fat"): (
        "low_fat",
        "The lower fat limit was kept: lean cooking methods are suggested.",
    ),
    ("low_carbs", "high_carbs"): (
        "low_carbs",
        "The lower carbohydrate limit was kept: high-carbohydrate staples are "
        "limited.",
    ),
    ("low_protein", "high_protein"): (
        "low_protein",
        "The protein restriction was kept because it is the medically "
        "protective one for the recorded condition.",
    ),
    ("increase_calories", "reduce_calories"): (
        "reduce_calories",
        "The calorie reduction was kept because it matches the stated goal, "
        "while the calorie increase is applied only as a condition-safe "
        "snack suggestion.",
    ),
}

#: Pairs that are NOT contradictory but still need a word of explanation:
#: both rules are kept and the plan is built so that they hold together.
#: The value is the explanation shown to the user.
COMPATIBLE_PAIRS: Dict[Tuple[str, str], str] = {
    ("vegetarian", "high_protein"): (
        "Both rules were kept: the protein comes from vegetarian sources such "
        "as dal, soy, paneer or curd, so no meat, fish or eggs are suggested."
    ),
    ("vegan", "high_protein"): (
        "Both rules were kept: the protein comes from plant sources such as "
        "dal, soy, nuts and seeds, so no animal product is suggested."
    ),
    ("vegan", "lactose_free"): (
        "Both rules were kept: dairy is already excluded, so the plan uses "
        "plant milks and curd alternatives."
    ),
    ("vegetarian", "lactose_free"): (
        "Both rules were kept: the plan uses lactose-free milk or curd "
        "alternatives alongside vegetarian protein sources."
    ),
    ("gluten_free", "low_carbs"): (
        "Both rules were kept: rice, millet or potato are used instead of "
        "wheat-based breads, and the portion sizes stay modest."
    ),
    ("increase_calories", "high_protein"): (
        "Both rules were kept: extra calories come from additional protein "
        "sources and side dishes rather than from sugar or refined carbs."
    ),
}

#: Canonical lookup view of ``CONTRADICTORY_PAIRS``: the key is the pair in
#: SORTED order, so a lookup never depends on how the pair was written down.
#: Without this, ``tuple(sorted((left, right)))`` would never match a key such
#: as ``("low_sodium", "high_sodium")`` and no contradiction would ever be
#: detected.
CONTRADICTORY_PAIRS_BY_SORTED_KEY: Dict[Tuple[str, str], Tuple[str, str]] = {
    tuple(sorted(pair)): value for pair, value in CONTRADICTORY_PAIRS.items()
}

#: Canonical lookup view of ``COMPATIBLE_PAIRS`` (same sorted-key rule).
COMPATIBLE_PAIRS_BY_SORTED_KEY: Dict[Tuple[str, str], str] = {
    tuple(sorted(pair)): value for pair, value in COMPATIBLE_PAIRS.items()
}

#: Conditions that make a strict calorie reduction unsafe.
CALORIE_REDUCTION_UNSAFE_CONDITIONS = (
    "pregnan",
    "breastfeed",
    "lactat",
    "underweight",
    "anorexi",
    "malnutriti",
    "type 1 diabetes",
)

#: Conditions that make high-intensity activity unsafe.
HIGH_INTENSITY_UNSAFE_CONDITIONS = (
    "cardiac",
    "heart",
    "angina",
    "hypertens",
    "uncontrolled blood pressure",
    "pregnan",
    "asthma",
    "copd",
)


def _normalise(text: Optional[str]) -> str:
    """Lower-case and collapse whitespace so keyword matching is stable.

    Hyphens and underscores are also turned into spaces so that the values the
    UI stores ("Gluten-free", "Dairy-free") match the keywords that are
    written with a space ("gluten free", "dairy free").
    """
    if not text:
        return ""
    lowered = str(text).lower()
    lowered = lowered.replace("-", " ").replace("_", " ")
    return " ".join(lowered.split())


def _split_list(text: Optional[str]) -> List[str]:
    """Split a comma/semicolon separated free-text field into its items."""
    if not text:
        return []
    normalised = str(text).replace(";", ",")
    return [part.strip() for part in normalised.split(",") if part.strip()]


def detect_rules(text: Optional[str]) -> List[str]:
    """Return the rule tokens recognised inside one free-text instruction."""
    haystack = _normalise(text)
    if not haystack:
        return []
    found: List[str] = []
    for token, keywords in RULE_KEYWORDS.items():
        if any(keyword in haystack for keyword in keywords):
            found.append(token)
    return found


def _conflict(
    code: str,
    title: str,
    explanation: str,
    severity: str,
    sources: Sequence[str],
    resolution: Optional[str] = None,
    requires_review: bool = False,
) -> Dict:
    """Build one conflict record in the shape stored/returned by the API."""
    return {
        "code": code,
        "title": title,
        "explanation": explanation,
        "severity": severity,
        "sources": list(sources),
        "resolution": resolution,
        "requires_review": requires_review,
    }


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def collect_restriction_sources(
    prescriptions: Optional[Sequence] = None,
    conditions: Optional[str] = None,
    allergies: Optional[str] = None,
    physical_limitations: Optional[str] = None,
) -> List[Tuple[str, str]]:
    """Build the (source_label, instruction_text) pairs to compare.

    SRS 3.3.4.1 requires instructions from more than one prescription OR
    condition profile to be accepted, so every prescription contributes its
    own labelled source and the health profile contributes one more.
    """
    sources: List[Tuple[str, str]] = []
    for index, prescription in enumerate(prescriptions or []):
        text = ""
        if isinstance(prescription, dict):
            text = prescription.get("restrictions") or ""
            label = prescription.get("source") or f"Prescription {index + 1}"
        else:
            text = getattr(prescription, "restrictions", "") or ""
            label = getattr(prescription, "source", None) or f"Prescription {index + 1}"
        if str(text).strip():
            sources.append((f"{label} restrictions", str(text)))

    for label, text in (
        ("Health conditions", conditions),
        ("Allergies", allergies),
        ("Physical limitations", physical_limitations),
    ):
        if str(text or "").strip():
            sources.append((label, str(text)))
    return sources


def find_dietary_conflicts(
    prescriptions: Optional[Sequence] = None,
    foods: Optional[Sequence[str]] = None,
    conditions: Optional[str] = None,
    allergies: Optional[str] = None,
    physical_limitations: Optional[str] = None,
    goals: Optional[str] = None,
) -> List[Dict]:
    """Detect conflicts between the dietary instructions available.

    ``prescriptions`` is a sequence of objects (or dicts) carrying a
    ``restrictions`` free-text field; ``conditions``/``allergies``/
    ``physical_limitations`` come from the health profile and ``goals`` from
    the patient's stated objective. ``foods`` is optional and only used to
    flag foods that violate an allergy.

    Returns a list of conflict dicts (see :func:`_conflict`). The list is
    empty when the instructions are compatible.
    """
    sources = collect_restriction_sources(
        prescriptions, conditions, allergies, physical_limitations
    )
    conflicts: List[Dict] = []

    # -- rule tokens per source ------------------------------------------- #
    token_sources: Dict[str, List[str]] = {}
    for label, text in sources:
        for token in detect_rules(text):
            token_sources.setdefault(token, []).append(label)

    # -- duplicates: the same rule from two different sources -------------- #
    for token, labels in token_sources.items():
        if len(labels) > 1:
            conflicts.append(
                _conflict(
                    CODE_DUPLICATE,
                    f"Duplicate restriction: {token.replace('_', ' ')}",
                    (
                        f"The {token.replace('_', ' ')} restriction appears in more "
                        f"than one source ({', '.join(labels)}). The repeated rule "
                        "is applied once, so the plan is not stricter than stated."
                    ),
                    SEVERITY_LOW,
                    labels,
                    resolution=(
                        "The duplicated restriction was applied once; both sources "
                        "agree, so no conflict remains."
                    ),
                    requires_review=False,
                )
            )

    # -- contradictions: two rules that cannot both hold ------------------- #
    tokens = sorted(token_sources)
    for index, left in enumerate(tokens):
        for right in tokens[index + 1:]:
            pair = tuple(sorted((left, right)))
            safe = CONTRADICTORY_PAIRS_BY_SORTED_KEY.get(pair)  # type: ignore[arg-type]
            if safe is None:
                continue
            kept, reason = safe
            labels = sorted(set(token_sources[left]) | set(token_sources[right]))
            conflicts.append(
                _conflict(
                    CODE_CONTRADICTORY,
                    f"Contradictory restrictions: {left.replace('_', ' ')} and {right.replace('_', ' ')}",
                    (
                        f"One source asks for {left.replace('_', ' ')} while another "
                        f"asks for {right.replace('_', ' ')}. These two rules "
                        "cannot both be satisfied at full strength."
                    ),
                    SEVERITY_MEDIUM,
                    labels,
                    resolution=reason,
                    requires_review=False,
                )
            )

    # -- compatible combinations worth explaining --------------------------- #
    for index, left in enumerate(tokens):
        for right in tokens[index + 1:]:
            pair = tuple(sorted((left, right)))
            explanation = COMPATIBLE_PAIRS_BY_SORTED_KEY.get(pair)
            if explanation is None:
                continue
            labels = sorted(set(token_sources[left]) | set(token_sources[right]))
            conflicts.append(
                _conflict(
                    CODE_COMPATIBLE,
                    f"Combined requirements: {left.replace('_', ' ')} and {right.replace('_', ' ')}",
                    explanation,
                    SEVERITY_LOW,
                    labels,
                    resolution=explanation,
                    requires_review=False,
                )
            )

    # -- goal vs. condition / prescription conflicts ----------------------- #
    conflicts.extend(_goal_conflicts(goals, sources, token_sources))

    # -- high-risk restrictions that need professional review -------------- #
    conflicts.extend(_high_risk_conflicts(sources, token_sources))

    # -- foods that violate a recorded allergy ---------------------------- #
    conflicts.extend(_food_conflicts(foods, allergies))

    return conflicts


# --------------------------------------------------------------------------- #
# Internal helpers
# --------------------------------------------------------------------------- #
def _goal_conflicts(
    goals: Optional[str],
    sources: Sequence[Tuple[str, str]],
    token_sources: Dict[str, List[str]],
) -> List[Dict]:
    """Detect a patient goal that fights a recorded condition/prescription.

    SRS 3.3.4.2 asks for "potentially incompatible recommendations" to be
    detected, and SRS 3.3.4.5 says an unresolved conflict must be flagged for
    review instead of silently choosing a rule.
    """
    conflicts: List[Dict] = []
    goal_text = _normalise(goals)
    if not goal_text:
        return conflicts

    condition_text = _normalise(" ".join(text for _, text in sources))

    # Calorie reduction vs. a condition that makes it unsafe.
    if any(marker in goal_text for marker in ("weight loss", "lose weight", "reduce weight", "slim")):
        if any(marker in condition_text for marker in CALORIE_REDUCTION_UNSAFE_CONDITIONS):
            conflicts.append(
                _conflict(
                    CODE_GOAL_CONDITION,
                    "Weight-loss goal may be unsafe for your recorded condition",
                    (
                        "Your goal requests a calorie reduction, but your recorded "
                        "health information suggests that lowering calories may be "
                        "unsafe for you. No calorie reduction was applied."
                    ),
                    SEVERITY_HIGH,
                    ["Patient goal", "Health conditions"],
                    resolution=(
                        "Your calorie target was kept at the maintenance level "
                        "instead of a reduced one."
                    ),
                    requires_review=True,
                )
            )

    # High-protein goal vs. a protein restriction from a prescription.
    if any(marker in goal_text for marker in ("muscle", "build muscle", "high protein", "protein gain")):
        if "low_protein" in token_sources:
            conflicts.append(
                _conflict(
                    CODE_GOAL_PRESCRIPTION,
                    "Protein goal conflicts with a protein restriction",
                    (
                        "Your goal asks for more protein, but a prescription "
                        "restricts protein intake. A safe balance cannot be "
                        "decided automatically."
                    ),
                    SEVERITY_HIGH,
                    ["Patient goal", "Prescription restrictions"],
                    resolution=None,
                    requires_review=True,
                )
            )

    # Calorie increase vs. a prescription that restricts calories.
    if any(marker in goal_text for marker in ("weight gain", "gain weight", "increase weight")):
        if "reduce_calories" in token_sources:
            conflicts.append(
                _conflict(
                    CODE_GOAL_PRESCRIPTION,
                    "Weight-gain goal conflicts with a calorie restriction",
                    (
                        "Your goal asks for a calorie increase while a prescription "
                        "restricts calories. The two cannot both be followed."
                    ),
                    SEVERITY_HIGH,
                    ["Patient goal", "Prescription restrictions"],
                    resolution=None,
                    requires_review=True,
                )
            )

    return conflicts


def _high_risk_conflicts(
    sources: Sequence[Tuple[str, str]],
    token_sources: Dict[str, List[str]],
) -> List[Dict]:
    """Flag restrictions that must be reviewed by a professional.

    SRS 3.3.4.5: unresolved or high-risk conflicts are flagged for
    doctor/nutritionist review rather than silently resolved.
    """
    conflicts: List[Dict] = []
    if "avoid_exercise" in token_sources:
        conflicts.append(
            _conflict(
                CODE_HIGH_RISK,
                "Activity must be cleared by a professional",
                (
                    "A recorded restriction limits physical activity. Only gentle, "
                    "low-impact suggestions are included and the plan is flagged "
                    "for professional review."
                ),
                SEVERITY_HIGH,
                token_sources["avoid_exercise"],
                resolution=(
                    "Gentle mobility and breathing activities are included; "
                    "vigorous activity is excluded."
                ),
                requires_review=True,
            )
        )
    return conflicts


def _food_conflicts(
    foods: Optional[Sequence[str]],
    allergies: Optional[str],
) -> List[Dict]:
    """Flag foods that match a recorded allergen."""
    conflicts: List[Dict] = []
    allergen_items = _split_list(allergies)
    if not allergen_items:
        return conflicts

    for food in foods or []:
        food_text = _normalise(food)
        if not food_text:
            continue
        hits = [item for item in allergen_items if _normalise(item) in food_text]
        if not hits:
            continue
        conflicts.append(
            _conflict(
                CODE_HIGH_RISK,
                f"Food matches a recorded allergen: {food}",
                (
                    f"'{food}' matches the recorded allergy/allergens "
                    f"({', '.join(hits)}) and is not included in the plan."
                ),
                SEVERITY_HIGH,
                ["Allergies"],
                resolution=f"'{food}' was excluded from the generated plan.",
                requires_review=False,
            )
        )
    return conflicts


# --------------------------------------------------------------------------- #
# Safe intersection
# --------------------------------------------------------------------------- #
def resolve_effective_restrictions(
    prescriptions: Optional[Sequence] = None,
    conditions: Optional[str] = None,
    allergies: Optional[str] = None,
    physical_limitations: Optional[str] = None,
) -> List[str]:
    """Return the rule tokens that can be safely applied together.

    SRS 3.3.4.4: "produce only compatible recommendations when a safe
    intersection of rules can be determined". Where two tokens contradict
    each other the stricter one wins (see ``CONTRADICTORY_PAIRS``), so the
    returned list never contains an unsatisfiable pair.
    """
    sources = collect_restriction_sources(
        prescriptions, conditions, allergies, physical_limitations
    )
    tokens: List[str] = []
    for _, text in sources:
        for token in detect_rules(text):
            if token not in tokens:
                tokens.append(token)

    # Drop the loser of every contradictory pair. The loop repeats until the
    # list is stable, because one token can take part in several pairs (e.g.
    # "high_protein" conflicts with both "low_protein" and "vegetarian").
    changed = True
    while changed:
        changed = False
        for pair, (kept, _reason) in CONTRADICTORY_PAIRS.items():
            left, right = pair
            if left in tokens and right in tokens:
                tokens.remove(right if kept == left else left)
                changed = True

    return tokens
