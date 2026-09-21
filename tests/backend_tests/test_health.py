from backend.app import create_app

class TestConfig:
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SECRET_KEY = "test-secret-key-with-at-least-thirty-two-characters"
    JWT_SECRET_KEY = "test-jwt-secret-key-with-at-least-thirty-two-characters"

def test_health_endpoint():
    app = create_app(TestConfig)
    response = app.test_client().get("/api/health")
    assert response.status_code == 200
    assert response.json["status"] == "ok"
