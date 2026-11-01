"""SCRUM-69 tests: bugs found in the Sprint 5 checks.

1. A peanut allergy must also remove generic nut items ("Roasted nuts"),
   because these mixes often contain peanuts; coconut stays allowed.
2. The frontend helper api.js shows FastAPI's "detail" message.
3. requirements.txt lists every package the FastAPI backend imports.
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key")

from backend.services import plan_generator as gen


def _allergic(item, allergies):
    return gen._matches_allergen(item, gen.allergen_terms(allergies))


def test_peanut_allergy_removes_roasted_nuts():
    assert _allergic("Roasted nuts", "peanuts")
    assert _allergic("Peanut chikki", "Peanuts")
    assert _allergic("Badam", "peanut")


def test_peanut_allergy_keeps_coconut_and_other_food():
    assert not _allergic("Coconut water", "peanuts")
    assert not _allergic("Seasonal fruit", "peanuts")
    assert not _allergic("Lentil curry", "peanuts")


def test_no_allergy_keeps_roasted_nuts():
    assert not _allergic("Roasted nuts", "None")


def test_api_helper_reads_fastapi_detail():
    text = (ROOT / "frontend" / "js" / "api.js").read_text(encoding="utf-8")
    assert "data.detail" in text and "Something went wrong." in text


def test_requirements_list_the_fastapi_backend_packages():
    lines = (ROOT / "requirements.txt").read_text(encoding="utf-8").lower()
    for package in ("fastapi", "uvicorn", "oracledb", "python-dotenv", "python-jose", "werkzeug", "pytest"):
        assert package in lines, package


def test_api_helper_lets_file_uploads_set_their_own_content_type():
    # A FormData body with a forced JSON Content-Type made the server drop the
    # prescription text and file (SCRUM-69 round 4).
    text = (ROOT / "frontend" / "js" / "api.js").read_text(encoding="utf-8")
    assert "instanceof FormData" in text


def test_prescriptions_page_keeps_every_element_the_script_uses():
    page = (ROOT / "frontend" / "pages" / "prescriptions.html").read_text(encoding="utf-8")
    script = (ROOT / "frontend" / "js" / "prescription.js").read_text(encoding="utf-8")
    for element_id in ("rx-list", "rx-status", "rx-add-form", "rx-add-btn", "rx-refresh", "rx-file",
                       "rx-file-name", "rx-count-total", "rx-count-active", "rx-count-pending"):
        assert f'id="{element_id}"' in page, element_id
        assert element_id in script, element_id
    for label in ("Add a prescription", "Add prescription", "Submitted prescriptions"):
        assert label in page, label
    assert page.count("data-rx-filter=") == 3
