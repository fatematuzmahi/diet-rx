from flask import Flask, jsonify
from flask_cors import CORS
from backend.config import Config
from backend.extensions import db, jwt, migrate

def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)
    CORS(app, resources={r"/api/*": {"origins": "*"}})
    db.init_app(app)
    migrate.init_app(app, db)
    jwt.init_app(app)
    from backend.routes.auth_routes import auth_bp
    from backend.routes.diet_routes import diet_bp
    from backend.routes.safety_routes import safety_bp
    from backend.routes.grocery_routes import grocery_bp
    app.register_blueprint(auth_bp, url_prefix="/api/auth")
    app.register_blueprint(diet_bp, url_prefix="/api/diets")
    app.register_blueprint(safety_bp, url_prefix="/api/safety")
    app.register_blueprint(grocery_bp, url_prefix="/api/grocery")
    @app.get("/api/health")
    def health_check():
        return jsonify({"status": "ok", "service": "DietRx API"})
    with app.app_context():
        from backend import models
        db.create_all()
    return app

app = create_app()

if __name__ == "__main__":
    app.run(debug=True)
