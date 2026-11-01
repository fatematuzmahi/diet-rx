"""DietRx browser (Selenium) tests: the user interface, end to end.

Each test opens Chrome, uses DietRx the way a user does, and checks what the
user sees. The backend and Live Server must be running; see conftest.py.

The tests only read data. They never save a plan, a log, a budget, a review,
a user, a dish or a price. The only request that sends data is the food safety
check, which looks a food up and stores nothing.
"""

import re

import pytest

pytest.importorskip("selenium", reason="Selenium is not installed: python -m pip install selenium")

from selenium.webdriver.common.action_chains import ActionChains  # noqa: E402
from selenium.webdriver.common.by import By  # noqa: E402
from selenium.webdriver.common.keys import Keys  # noqa: E402
from selenium.webdriver.support import expected_conditions as EC  # noqa: E402

# Pages with a sign-in guard in their <head>.
PROTECTED_PAGES = [
    "dashboard.html", "diet_plan.html", "meal-plan.html", "drift.html",
    "grocery.html", "prices.html", "budget.html", "swaps.html",
    "prescriptions.html", "sharing.html",
]
MENU_GROUPS = ["My plan", "Tracking", "Food & budget", "Health"]
PASSWORD_HASH = re.compile(r"^(scrypt|pbkdf2)[:$]")


def open_groups(ui):
    return [g.find_element(By.TAG_NAME, "summary").text.strip()
            for g in ui.all(".app-nav details.nav-group") if g.get_attribute("open") is not None]


# =========================================================================== #
# 1. Public pages (no sign-in needed)
# =========================================================================== #
def test_ui01_login_page_opens_with_empty_fields(ui):
    ui.clear_sign_in()
    assert ui.driver.title == "Login | DietRx"
    email, password = ui.visible("#email"), ui.css("#password")
    assert email.get_attribute("value") == "" and password.get_attribute("value") == ""
    # read-only until clicked, so the browser cannot autofill a saved login
    assert email.get_attribute("readonly") is not None
    assert password.get_attribute("type") == "password"


def test_ui02_wrong_password_shows_a_clear_message(ui):
    ui.clear_sign_in()
    # an address that is not registered: no real account is touched
    ui.type_into("#email", "selenium.no.such.user@example.com")
    ui.type_into("#password", "Wrong-Password-1")
    ui.css("#login-form button[type=submit]").click()
    alert = ui.wait.until(EC.alert_is_present())
    message = alert.text
    alert.accept()
    assert message == "Invalid email or password."
    assert ui.page() == "login.html"
    assert ui.storage("access_token") is None


@pytest.mark.parametrize("page", PROTECTED_PAGES)
def test_ui03_protected_pages_send_a_signed_out_user_to_login(ui, page):
    ui.clear_sign_in()
    ui.open(page)
    ui.wait_for_page("login.html")


def test_ui04_login_page_links_to_register_forgot_doctor_and_admin(ui):
    ui.clear_sign_in()
    links = {a.get_attribute("href").rsplit("/", 1)[-1] for a in ui.all("main a[href]")}
    assert {"register.html", "forgot-password.html", "doctor.html", "admin.html"} <= links


# =========================================================================== #
# 2. Patient
# =========================================================================== #
def test_ui05_patient_login_opens_the_dashboard(ui, patient):
    ui.login_patient(*patient)
    assert ui.storage("access_token")
    ui.visible("#db-greeting")
    assert ui.text("#db-greeting") in {"Good morning", "Good afternoon", "Good evening"}
    assert ui.text("#db-name") not in {"", "there"}          # the patient's own name
    summaries = [s.text.strip() for s in ui.all(".app-nav details.nav-group > summary")]
    assert summaries == MENU_GROUPS
    assert ui.css(".app-nav [data-nav-logout]").text.strip() == "Logout"


def test_ui06_only_one_menu_group_is_open_at_a_time(ui, patient):
    ui.login_patient(*patient)
    summaries = ui.all(".app-nav details.nav-group > summary")
    summaries[0].click()
    ui.wait.until(lambda d: open_groups(ui) == ["My plan"])
    ui.pause()
    summaries[1].click()
    ui.wait.until(lambda d: open_groups(ui) == ["Tracking"])   # My plan closed by itself
    ui.pause()
    ActionChains(ui.driver).send_keys(Keys.ESCAPE).perform()
    ui.wait.until(lambda d: open_groups(ui) == [])             # Escape closes the menu


def test_ui07_menu_opens_budget_and_prices_with_the_price_table(ui, patient):
    ui.login_patient(*patient)
    group = ui.all(".app-nav details.nav-group > summary")[2]       # Food & budget
    group.click()
    ui.visible(".app-nav a[href='prices.html']").click()
    ui.wait_for_page("prices.html")
    assert ui.visible("h1").text.strip() == "Plan meals that fit your pocket"
    ui.text_changes_from("#pb-rows", "Loading prices")
    rows = [r for r in ui.all("#pb-rows tr") if not r.find_elements(By.CSS_SELECTOR, ".pb-empty")]
    assert rows, "the price table should list foods with prices"
    assert re.fullmatch(r"\d+ items?", ui.text("#pb-count"))


def test_ui08_diet_plan_page_loads_without_errors(ui, patient):
    ui.login_patient(*patient)
    ui.open("diet_plan.html")
    ui.wait.until(lambda d: ui.css("#plan-result").is_displayed() or ui.css("#pl-empty").is_displayed()
                  or "is-error" in ui.css("#plan-status").get_attribute("class"))
    assert "is-error" not in ui.css("#plan-status").get_attribute("class"), ui.text("#plan-status")


def test_ui09_smart_swaps_page_loads_without_errors(ui, patient):
    ui.login_patient(*patient)
    ui.open("swaps.html")
    assert ui.visible("h1").text.strip() == "Smart swaps"
    ui.wait.until(lambda d: ui.text("#sw-status-pill") != "…"
                  or "is-error" in ui.css("#sw-status").get_attribute("class"))
    assert "is-error" not in ui.css("#sw-status").get_attribute("class"), ui.text("#sw-status")
    assert re.fullmatch(r"\d+ ideas?", ui.text("#sw-count"))


def test_ui10_safety_checker_says_avoid_for_an_allergy(ui, patient):
    ui.login_patient(*patient)
    ui.open("safety_checker.html")
    ui.type_into("#food", "Peanut chikki")
    ui.type_into("#allergies", "peanut")
    ui.css("#check-button").click()
    ui.wait.until(lambda d: ui.text("#result-status").startswith(("Status:", "Unable")))
    assert ui.text("#result-status") == "Status: Avoid"
    assert "peanut" in ui.text("#result-reasons").lower()
    assert ui.text("#review-note") == "Professional review recommended."


def test_ui11_dashboard_fits_a_phone_screen(ui, patient):
    ui.login_patient(*patient)
    ui.driver.execute_cdp_cmd("Emulation.setDeviceMetricsOverride", {
        "width": 390, "height": 844, "deviceScaleFactor": 3, "mobile": True})
    try:
        ui.driver.refresh()
        ui.visible("#db-greeting")
        widths = ui.driver.execute_script(
            "return [document.documentElement.scrollWidth, window.innerWidth];")
        assert widths[0] <= widths[1] + 1, f"page is wider than the phone: {widths}"
    finally:
        ui.driver.execute_cdp_cmd("Emulation.clearDeviceMetricsOverride", {})


def test_ui12_patient_cannot_open_the_admin_or_doctor_panel(ui, patient):
    ui.login_patient(*patient)
    ui.open("admin.html")
    ui.visible("#adminLogin")
    assert not ui.css("#adminApp").is_displayed()
    ui.open("doctor.html")
    ui.visible("#dc-login")
    assert not ui.css("#dc-portal").is_displayed()


def test_ui13_logout_clears_the_sign_in_and_blocks_going_back(ui, patient):
    ui.login_patient(*patient)
    ui.css(".app-nav [data-nav-logout]").click()
    ui.wait_for_page("login.html")
    for key in ("access_token", "session_token", "user"):
        assert ui.storage(key) is None
    ui.driver.back()
    ui.pause()
    ui.open("dashboard.html")
    ui.wait_for_page("login.html")


# =========================================================================== #
# 3. Doctor / nutritionist (view only: no review is saved)
# =========================================================================== #
def test_ui14_doctor_login_shows_review_list_and_my_patients(ui, doctor):
    ui.login_doctor(*doctor)
    assert ui.text("#dc-hello").startswith("Welcome")
    ui.text_changes_from("#dc-patients", "Loading")
    ui.text_changes_from("#dc-queue", "Loading")
    assert "is-error" not in ui.css("#dc-status").get_attribute("class"), ui.text("#dc-status")
    assert ui.text("#dc-patient-count").isdigit()
    assert ui.storage("session_token") is None          # a doctor is not a patient session


def test_ui15_doctor_logout_returns_to_the_doctor_login(ui, doctor):
    ui.login_doctor(*doctor)
    ui.visible("#logoutBtn").click()
    ui.visible("#dc-login")
    assert ui.storage("access_token") is None


# =========================================================================== #
# 4. Admin (view only: nothing is saved)
# =========================================================================== #
def test_ui16_admin_sees_the_users_table_without_passwords(ui, admin):
    ui.login_admin(*admin)
    headers = [th.text.strip() for th in ui.all("#users thead th")]
    assert headers == ["ID", "Name", "Email", "Role", "Created At"]
    ui.text_changes_from("#usersTableBody", "Loading users")
    assert ui.text("#usersTableBody") != "Unable to load users."
    assert ui.text("#statusMessage") == "Users loaded."
    for cell in ui.all("#usersTableBody td"):
        assert not PASSWORD_HASH.match(cell.text.strip()), "a password hash is visible"


def test_ui17_admin_tabs_open_dish_dna_and_food_prices(ui, admin):
    ui.login_admin(*admin)
    for tab, panel in (("dish-dna", "#dish-dna"), ("food-prices", "#food-prices"), ("users", "#users")):
        ui.css(f".tab[data-tab='{tab}']").click()
        ui.visible(panel)
        others = [p for p in ui.all("#adminApp .panel") if p.get_attribute("id") != panel[1:]]
        assert all(not p.is_displayed() for p in others)


def test_ui18_admin_sign_out_shows_the_sign_in_form(ui, admin):
    ui.login_admin(*admin)
    ui.visible("#logoutButton").click()
    ui.visible("#adminLogin")
    assert ui.storage("access_token") is None


# =========================================================================== #
# 5. API documentation (Swagger)
# =========================================================================== #
def test_ui19_swagger_docs_show_authorize_and_all_endpoints(ui):
    ui.open_api("/docs")
    ui.visible(".swagger-ui .btn.authorize")
    ui.wait.until(lambda d: len(ui.all(".opblock")) >= 70)
    assert len(ui.all(".opblock")) >= 70
