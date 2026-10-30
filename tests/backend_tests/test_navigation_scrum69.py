"""SCRUM-69 (round 3) tests: one menu on every patient page, clean sign-in forms.

Found in SCRUM-68 (UAT):
1. Each patient page had its own menu (Sharing missing on most pages,
   Prescriptions had no Dashboard link, Safety Checker had no Logout).
2. Sign-in forms showed the saved e-mail and password again after logout and
   after a refresh.
These checks read the HTML files only; no server or database is needed.
"""

from html.parser import HTMLParser
from pathlib import Path

import pytest

PAGES = Path(__file__).resolve().parents[2] / "frontend" / "pages"

PATIENT_PAGES = [
    "dashboard.html", "diet_plan.html", "meal-plan.html", "drift.html", "dish_dna.html",
    "daily_log.html", "progress.html", "notifications.html", "grocery.html", "prices.html",
    "budget.html", "swaps.html", "prescriptions.html", "safety_checker.html", "sharing.html",
]
SIGN_IN_FORMS = {
    "login.html": ("email", "password"),
    "doctor.html": ("dc-email", "dc-password"),
    "admin.html": ("adminEmail", "adminPassword"),
}


class Page(HTMLParser):
    """Collects the main-menu links, stylesheets, scripts and inputs of one page."""

    def __init__(self):
        super().__init__()
        self.nav_depth = 0
        self.menu, self.current, self.links, self.scripts, self.inputs, self.forms = [], [], [], [], {}, []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "nav" and "app-nav" in (a.get("class") or ""):
            self.nav_depth += 1
        elif tag == "nav" and self.nav_depth:
            self.nav_depth += 1
        if tag == "a" and self.nav_depth:
            self.menu.append(a.get("href"))
            if a.get("aria-current") == "page":
                self.current.append(a.get("href"))
        if tag == "link":
            self.links.append(a.get("href"))
        if tag == "script" and a.get("src"):
            self.scripts.append(a["src"])
        if tag == "input" and a.get("id"):
            self.inputs[a["id"]] = a
        if tag == "form":
            self.forms.append(a)

    def handle_endtag(self, tag):
        if tag == "nav" and self.nav_depth:
            self.nav_depth -= 1


def parse(name):
    page = Page()
    page.feed((PAGES / name).read_text(encoding="utf-8"))
    return page


EXPECTED_MENU = sorted(PATIENT_PAGES + ["#"])


@pytest.mark.parametrize("name", PATIENT_PAGES)
def test_every_patient_page_has_the_same_full_menu(name):
    assert sorted(parse(name).menu) == EXPECTED_MENU


@pytest.mark.parametrize("name", PATIENT_PAGES)
def test_the_menu_marks_only_the_open_page(name):
    assert parse(name).current == [name]


@pytest.mark.parametrize("name", PATIENT_PAGES)
def test_every_patient_page_loads_the_shared_menu_files(name):
    page = parse(name)
    assert "../css/nav.css" in page.links
    assert "../js/nav.js" in page.scripts


def test_logout_works_on_every_page_from_the_shared_script():
    script = (PAGES.parent / "js" / "nav.js").read_text(encoding="utf-8")
    assert "data-nav-logout" in script and "access_token" in script and "login.html" in script
    for name in PATIENT_PAGES:
        assert 'id="logoutBtn"' in (PAGES / name).read_text(encoding="utf-8"), name


@pytest.mark.parametrize("name", sorted(SIGN_IN_FORMS))
def test_sign_in_forms_start_empty(name):
    page = parse(name)
    assert any("data-no-autofill" in form for form in page.forms)
    for field in SIGN_IN_FORMS[name]:
        attrs = page.inputs[field]
        assert attrs.get("autocomplete") == "off"
        assert "readonly" in attrs
    assert "../js/no_autofill.js" in page.scripts


def test_login_page_keeps_all_its_links():
    html = (PAGES / "login.html").read_text(encoding="utf-8")
    for target in ("forgot-password.html", "doctor.html", "admin.html", "register.html"):
        assert f'href="{target}"' in html, target
