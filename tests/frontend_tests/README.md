# Frontend tests (Selenium)

`test_ui_selenium.py` opens Chrome and uses DietRx the way a user does:
it signs in, opens the menu and pages, and checks what is shown on screen.
The tests only read data. They never save a plan, log, budget, review, user,
dish or price.

| No. | What is checked |
|-----|-----------------|
| UI-01 | Login page opens with empty, read-only fields (no autofill) |
| UI-02 | A wrong login shows "Invalid email or password." |
| UI-03 | 10 protected pages send a signed-out user to the login page |
| UI-04 | Login page links to Register, Forgot password, Doctor and Admin login |
| UI-05 | Patient login opens the Dashboard with the grouped menu |
| UI-06 | Only one menu group is open at a time; Escape closes it |
| UI-07 | Menu → Budget & prices shows the local price table |
| UI-08 | Diet plan page loads without an error |
| UI-09 | Smart swaps page loads without an error |
| UI-10 | Safety checker says "Avoid" for a food in the allergy list |
| UI-11 | Dashboard fits a 390 px phone screen (no sideways scrolling) |
| UI-12 | A patient cannot open the Admin panel or the Doctor view |
| UI-13 | Logout clears the sign-in and the Dashboard cannot be reopened |
| UI-14 | Doctor login shows the review list and My patients |
| UI-15 | Doctor logout returns to the doctor login |
| UI-16 | Admin sees the Users table, with no password column or hash |
| UI-17 | Admin tabs open Dish DNA and Food Prices (view only) |
| UI-18 | Admin sign out shows the sign-in form |
| UI-19 | Swagger `/docs` shows Authorize and all endpoints |

## Run

1. Start the backend: `python -m uvicorn backend.main:app --reload`
2. Start Live Server on the project folder (port 5500).
3. Install Selenium once: `python -m pip install selenium`
4. In PowerShell, set the test accounts for this terminal only
   (they are not saved anywhere):

   ```powershell
   $env:DIETRX_UI_PATIENT_EMAIL="..."; $env:DIETRX_UI_PATIENT_PASSWORD="..."
   $env:DIETRX_UI_DOCTOR_EMAIL="...";  $env:DIETRX_UI_DOCTOR_PASSWORD="..."
   $env:DIETRX_UI_ADMIN_EMAIL="...";   $env:DIETRX_UI_ADMIN_PASSWORD="..."
   ```

5. Run:

   ```powershell
   python -m pytest tests/frontend_tests -v --junitxml=docs/test_results/selenium_ui.xml
   ```

If a server is not running, or Selenium is not installed, the tests are
skipped, not failed. Tests for a role without an e-mail and password are
skipped too. On a failure, a screenshot is saved in
`tests/frontend_tests/screenshots/` (this folder is not committed).

Optional: `$env:DIETRX_UI_SLOW="0.8"` slows each step down (for a video),
`$env:DIETRX_UI_HEADLESS="1"` runs Chrome without a window, and
`$env:DIETRX_UI_BASE_URL` changes the page address if Live Server uses a
different folder (default `http://127.0.0.1:5500/frontend/pages`).
