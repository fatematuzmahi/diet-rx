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