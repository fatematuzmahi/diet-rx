from backend.app import create_app
from backend.extensions import db


class TestConfig:
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SECRET_KEY = "test-secret-key-with-at-least-thirty-two-characters"
    JWT_SECRET_KEY = "test-jwt-secret-key-with-at-least-thirty-two-characters"


def make_client():
    app = create_app(TestConfig)
    return app, app.test_client()


def test_user_can_register_and_login():
    app, client = make_client()
    registration = client.post(
        "/api/auth/register",
        json={"name": "Test User", "email": "test@example.com", "password": "password123"},
    )
    login = client.post(
        "/api/auth/login",
        json={"email": "test@example.com", "password": "password123"},
    )
    assert registration.status_code == 201
    assert login.status_code == 200
    assert login.json["access_token"]
    with app.app_context():
        db.session.remove()


def test_safety_checker_requires_food_name():
    _, client = make_client()
    response = client.post("/api/safety/check", json={})
    assert response.status_code == 200
    assert response.json["status"] == "error"
