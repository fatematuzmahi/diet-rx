
# DietRx

## 1. What is DietRx?

DietRx is a decision-support system for prescription-aware diet planning, food safety, and budget-friendly grocery support.

It helps users manage personalized meal plans and related dietary information.

DietRx is a decision-support system and does not replace professional medical advice.

## 2. Tech Stack

- Python 3.13
- FastAPI and Uvicorn
- Pydantic
- Oracle Database with `python-oracledb`
- JWT authentication with `python-jose`
- Password hashing with Werkzeug
- HTML, CSS, and JavaScript
- Pytest
- Git and GitHub
- Jira

## 3. Folder Structure

```text
diet-rx/
├── backend/
│   ├── routers/
│   ├── services/
│   ├── schemas/
│   └── database/
├── frontend/
│   ├── pages/
│   ├── css/
│   └── js/
├── tests/
│   └── backend_tests/
├── README.md
├── .env.example
└── requirements.txt
```

## 4. Installation

Python 3.13 is required.

```bash
git clone https://github.com/fatematuzmahi/diet-rx
cd diet-rx
git checkout unique-branch
python -m pip install -r requirements.txt
```

## 5. Environment Configuration

Create a local `.env` file from `.env.example`:

```powershell
Copy-Item .env.example .env
```

Configure the database, JWT, and SMTP variables in `.env`.

Never commit `.env` or publish real credentials.

**Important:** `JWT_SECRET_KEY` must not be empty. The server will not start without it (SCRUM-62).

The `.env.example` file contains the required variable names and example configuration values without real credentials.

## 6. Database Setup

Run these SQL files against the Oracle database in this order:

1. `backend/database/schema.sql`
2. `backend/database/prescription_scrum46.sql`
3. `backend/database/dish_dna_scrum47.sql`
4. `backend/database/dish_dna_seed_scrum47.sql`
5. `backend/database/personalized_plan_scrum48.sql`

Then, from the project root, run these Python scripts in order:

```bash
python backend/database/apply_meal_plan_scrum49.py
python backend/database/apply_notification_scrum51.py
python backend/database/apply_daily_log_scrum53.py
python backend/database/apply_food_price_scrum57.py
python backend/database/apply_meal_swap_scrum59.py
python backend/database/apply_sharing_scrum56.py
python -m backend.database.apply_admin_user
```

On the shared team database, only the team lead runs these scripts.

## 7. Run the Backend

Start the backend from the project root:

```bash
python -m uvicorn backend.main:app --reload
```

API base URL:

`http://127.0.0.1:8000`

Swagger UI:

`http://127.0.0.1:8000/docs`

To test protected endpoints in Swagger UI:

1. Open `/auth/login` and log in to obtain an access token.
2. Click **Authorize** at the top of Swagger UI.
3. Paste the token into the authorization field and authorize.
4. Test the protected endpoints.

## 8. Run the Frontend

Open the project in VS Code and use the Live Server extension.

Open:

`http://127.0.0.1:5500/frontend/pages/login.html`

The backend CORS configuration allows the frontend origin on port `5500`.

## 9. Run Tests

Run the backend tests from the project root:

```bash
python -m pytest tests/backend_tests -v
```

The last reported test result was **418 passed**. Run the command again to verify the current result.

The tests use a fake database, so a live database connection is not required to run them.

## 10. Provision the Admin Account

After completing the database setup, run:

```bash
python -m backend.database.provision_admin
```

Enter the admin password when prompted. Do not hardcode the password in source code or commit credentials.
