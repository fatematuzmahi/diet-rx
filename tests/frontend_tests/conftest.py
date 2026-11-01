"""Shared set-up for the DietRx Selenium (browser) tests.

These tests drive a real Chrome window against the running app:

    backend   python -m uvicorn backend.main:app --reload   -> http://127.0.0.1:8000
    frontend  VS Code Live Server                            -> http://127.0.0.1:5500

If either server is not running, or Selenium is not installed, every test here
is SKIPPED (not failed), so `python -m pytest tests/backend_tests` is unaffected.

Sign-in details are read from environment variables only. They are never
written in this code:

    DIETRX_UI_PATIENT_EMAIL   DIETRX_UI_PATIENT_PASSWORD
    DIETRX_UI_DOCTOR_EMAIL    DIETRX_UI_DOCTOR_PASSWORD
    DIETRX_UI_ADMIN_EMAIL     DIETRX_UI_ADMIN_PASSWORD

Tests for a role whose details are missing are skipped.

Optional settings:

    DIETRX_UI_BASE_URL   where the pages are served
                         (default http://127.0.0.1:5500/frontend/pages)
    DIETRX_API_URL       backend address (default http://127.0.0.1:8000)
    DIETRX_UI_HEADLESS   1 = run Chrome without a window
    DIETRX_UI_SLOW       seconds to pause after each step (e.g. 0.8 for a video)
"""

import os
import time
import urllib.request
from pathlib import Path

import pytest

try:
    from selenium import webdriver
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.support.ui import WebDriverWait
    HAVE_SELENIUM = True
except ImportError:  # the test file skips itself with a clear message
    HAVE_SELENIUM = False

BASE_URL = os.environ.get("DIETRX_UI_BASE_URL", "http://127.0.0.1:5500/frontend/pages").rstrip("/")
API_URL = os.environ.get("DIETRX_API_URL", "http://127.0.0.1:8000").rstrip("/")
HEADLESS = os.environ.get("DIETRX_UI_HEADLESS", "0") == "1"
SLOW = float(os.environ.get("DIETRX_UI_SLOW", "0") or 0)
WAIT_SECONDS = 20  # the shared Oracle database can be slow to answer

SCREENSHOT_DIR = Path(__file__).resolve().parent / "screenshots"


def _reachable(url):
    try:
        with urllib.request.urlopen(url, timeout=3) as response:
            return response.status < 500
    except Exception:
        return False


@pytest.fixture(scope="session")
def servers():
    """Skip everything if the backend or the Live Server is not running."""
    if not _reachable(f"{API_URL}/health"):
        pytest.skip(f"Backend is not running at {API_URL} "
                    "(start it with: python -m uvicorn backend.main:app --reload)")
    if not _reachable(f"{BASE_URL}/login.html"):
        pytest.skip(f"Frontend is not reachable at {BASE_URL}/login.html "
                    "(start Live Server, or set DIETRX_UI_BASE_URL)")
    return {"base": BASE_URL, "api": API_URL}


def _credentials(role):
    email = os.environ.get(f"DIETRX_UI_{role}_EMAIL", "").strip()
    password = os.environ.get(f"DIETRX_UI_{role}_PASSWORD", "")
    if not email or not password:
        pytest.skip(f"Set DIETRX_UI_{role}_EMAIL and DIETRX_UI_{role}_PASSWORD to run this test")
    return email, password


class UI:
    """Small helpers so each test reads like the user's steps."""

    def __init__(self, driver):
        self.driver = driver
        self.wait = WebDriverWait(driver, WAIT_SECONDS)

    # ---- navigation -------------------------------------------------------
    def open(self, page):
        self.driver.get(f"{BASE_URL}/{page}")
        self.pause()

    def open_api(self, path):
        self.driver.get(f"{API_URL}{path}")
        self.pause()

    def pause(self):
        if SLOW:
            time.sleep(SLOW)

    def clear_sign_in(self):
        """Start signed out: open a public page and empty its local storage."""
        self.open("login.html")
        self.driver.execute_script("window.localStorage.clear();")

    def page(self):
        return self.driver.current_url.split("?")[0].split("#")[0].rsplit("/", 1)[-1]

    def wait_for_page(self, name):
        self.wait.until(lambda d: self.page() == name)
        self.pause()

    # ---- elements ---------------------------------------------------------
    def css(self, selector):
        return self.driver.find_element(By.CSS_SELECTOR, selector)

    def all(self, selector):
        return self.driver.find_elements(By.CSS_SELECTOR, selector)

    def visible(self, selector):
        element = self.wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, selector)))
        self.pause()
        return element

    def text(self, selector):
        return self.css(selector).text.strip()

    def type_into(self, selector, value):
        """Click first: sign-in fields stay read-only until clicked (no autofill)."""
        field = self.visible(selector)
        field.click()
        field.clear()
        field.send_keys(value)

    def storage(self, key):
        return self.driver.execute_script("return window.localStorage.getItem(arguments[0]);", key)

    def text_changes_from(self, selector, *waiting_texts):
        """Wait until an element no longer shows a 'Loading…' style text."""
        self.wait.until(lambda d: self.text(selector) and
                        not any(t in self.text(selector) for t in waiting_texts))
        self.pause()
        return self.text(selector)

    # ---- sign in ----------------------------------------------------------
    def login_patient(self, email, password):
        self.clear_sign_in()
        self.type_into("#email", email)
        self.type_into("#password", password)
        self.css("#login-form button[type=submit]").click()
        self.wait_for_page("dashboard.html")

    def login_doctor(self, email, password):
        self.clear_sign_in()
        self.open("doctor.html")
        self.type_into("#dc-email", email)
        self.type_into("#dc-password", password)
        self.css("#dc-login-btn").click()
        self.visible("#dc-portal")

    def login_admin(self, email, password):
        self.clear_sign_in()
        self.open("admin.html")
        self.type_into("#adminEmail", email)
        self.type_into("#adminPassword", password)
        self.css("#adminLoginButton").click()
        self.visible("#adminApp")


@pytest.fixture
def driver(servers):
    options = webdriver.ChromeOptions()
    if HEADLESS:
        options.add_argument("--headless=new")
    options.add_argument("--window-size=1366,900")
    # Keep the test browser clean: no password-save pop-ups.
    options.add_experimental_option("prefs", {
        "credentials_enable_service": False,
        "profile.password_manager_enabled": False,
    })
    browser = webdriver.Chrome(options=options)  # Selenium Manager finds the driver
    yield browser
    browser.quit()


@pytest.fixture
def ui(driver):
    return UI(driver)


@pytest.fixture
def patient():
    return _credentials("PATIENT")


@pytest.fixture
def doctor():
    return _credentials("DOCTOR")


@pytest.fixture
def admin():
    return _credentials("ADMIN")


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """On a failure, save a screenshot of the browser to tests/frontend_tests/screenshots/."""
    outcome = yield
    report = outcome.get_result()
    if report.when == "call" and report.failed and "driver" in item.fixturenames:
        browser = item.funcargs.get("driver")
        if browser is not None:
            SCREENSHOT_DIR.mkdir(exist_ok=True)
            try:
                browser.save_screenshot(str(SCREENSHOT_DIR / f"{item.name}.png"))
            except Exception:
                pass
