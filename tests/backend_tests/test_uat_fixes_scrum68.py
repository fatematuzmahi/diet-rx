"""SCRUM-68 tests: bugs found during the user acceptance test (UAT).

1. "Low salt" and hypertension must give the low-sodium rule, and a
   low-sodium plan must leave out very salty items such as papad.
2. "Avoid sweets" must give the low-sugar rule.
3. Every patient page is guarded, also when the browser shows it again
   with the Back button after logout (nav.js).
4. An empty prescription is not saved (prescription.js).
5. Plan times are shown in local time (the server stores UTC).
6. The dashboard refreshes the activity guidance after the profile is saved.
7. The login page links to email verification and sends an unverified
   account there.
"""

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key")

from backend.services import plan_generator as gen
from backend.services.plan_conflict_resolver import (
    detect_rules,
    resolve_effective_restrictions,
)

JS = ROOT / "frontend" / "js"
PAGES = ROOT / "frontend" / "pages"


def _read(path):
    return path.read_text(encoding="utf-8")


def _uat_plan():
    """The same inputs as the UAT patient account."""
    return gen.generate_plan(
        patient_id=1,
        age=22,
        gender="Female",
        weight_kg=55,
        height_cm=158,
        activity_level="Light",
        goals="Control blood sugar, improve energy",
        conditions="Hypertension, Diabetes",
        allergies="Peanuts",
        food_preference="Halal",
        prescriptions=[{"restrictions": "Low salt\nAvoid sweets\nNo sugary drinks", "source": "PATIENT"}],
        days=7,
    )


# 1-2. salt and sweets wording ------------------------------------------------ #
def test_low_salt_wording_gives_the_low_sodium_rule():
    for text in ("Low salt", "avoid salt", "No added salt", "salt restricted diet"):
        assert "low_sodium" in detect_rules(text), text


def test_hypertension_gives_the_low_sodium_rule():
    assert "low_sodium" in detect_rules("Hypertension")
    assert "low_sodium" in detect_rules("high blood pressure")
    assert "low_sodium" in resolve_effective_restrictions(conditions="Hypertension, Diabetes")


def test_avoid_sweets_gives_the_low_sugar_rule():
    assert "low_sugar" in detect_rules("Avoid sweets")
    assert "low_sugar" in detect_rules("no sweets after dinner")


def test_the_uat_plan_applies_both_rules():
    plan = _uat_plan()
    assert "low_sodium" in plan["restrictions_applied"]
    assert "low_sugar" in plan["restrictions_applied"]


def test_low_sodium_plan_leaves_out_papad_and_keeps_every_meal():
    plan = _uat_plan()
    items = [item for day in plan["days"] for meal in day["meals"] for item in meal["items"]]
    assert not any("papad" in item.lower() or "pickle" in item.lower() for item in items)
    assert all(meal["items"] for day in plan["days"] for meal in day["meals"])


def test_peanut_allergy_still_removes_nuts_in_the_uat_plan():
    plan = _uat_plan()
    items = [item.lower() for day in plan["days"] for meal in day["meals"] for item in meal["items"]]
    assert not any("peanut" in item or "nut" in item.split() or "chikki" in item for item in items)


def test_the_stricter_salt_rule_still_wins():
    tokens = resolve_effective_restrictions(
        prescriptions=[
            {"restrictions": "low salt", "source": "Dr A"},
            {"restrictions": "high sodium", "source": "Dr B"},
        ]
    )
    assert "low_sodium" in tokens and "high_sodium" not in tokens


# 3. Back button after logout ------------------------------------------------- #
def test_nav_script_guards_every_patient_page():
    script = _read(JS / "nav.js")
    assert 'localStorage.getItem("access_token")' in script
    assert 'window.location.replace("login.html")' in script
    assert 'addEventListener("pageshow", guard)' in script


def test_every_page_with_the_patient_menu_loads_the_guard():
    for page in PAGES.glob("*.html"):
        html = _read(page)
        if "data-nav-logout" in html:
            assert "../js/nav.js" in html, page.name


def test_login_and_staff_pages_do_not_load_the_patient_guard():
    for name in ("login.html", "register.html", "verify-email.html", "doctor.html", "admin.html"):
        assert "../js/nav.js" not in _read(PAGES / name), name


# 4. empty prescription -------------------------------------------------------- #
def test_empty_prescription_is_not_sent():
    script = _read(JS / "prescription.js")
    start = script.index("async function submitAdd")
    body = script[start:script.index("apiRequest(", start)]
    assert "rulesBox.value.trim()" in body
    assert "return;" in body


# 5. plan times ----------------------------------------------------------------- #
def test_plan_times_are_read_as_utc():
    for name in ("plan.js", "meal-plan.js"):
        script = _read(JS / name)
        assert re.search(r"\$\{text\}Z", script), name


# 6. activity guidance refresh ---------------------------------------------------- #
def test_saving_the_profile_refreshes_the_guidance():
    assert 'dispatchEvent(new CustomEvent("dietrx:profile-saved"))' in _read(JS / "health_profile.js")
    assert 'addEventListener("dietrx:profile-saved", loadGuidance)' in _read(JS / "dashboard_home.js")


# 7. email verification ------------------------------------------------------------ #
def test_login_page_links_to_email_verification():
    assert 'href="verify-email.html"' in _read(PAGES / "login.html")


def test_unverified_login_goes_to_the_verify_page_with_the_email():
    script = _read(JS / "auth.js")
    assert "/verify your email/i" in script
    assert 'verify-email.html?email=' in script
