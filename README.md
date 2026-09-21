# DietRx

University course project for prescription-aware diet planning, food safety, and budget grocery support.

## Stack

HTML, CSS and Vanilla JavaScript; Flask REST API; SQLAlchemy; MySQL (SQLite for local development); JWT authentication.

## Run locally

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
py backend/app.py
```

Open `frontend/index.html`. The API is at `http://127.0.0.1:5000/api`.
