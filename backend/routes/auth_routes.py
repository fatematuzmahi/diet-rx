from flask import Blueprint, jsonify, request
from flask_jwt_extended import create_access_token
from backend.extensions import db
from backend.models.user import User

auth_bp = Blueprint("auth", __name__)

@auth_bp.post("/register")
def register():
    data = request.get_json(silent=True) or {}
    name, email, password = data.get("name", "").strip(), data.get("email", "").strip().lower(), data.get("password", "")
    if not all([name, email, password]): return jsonify({"message": "Name, email, and password are required."}), 400
    if User.query.filter_by(email=email).first(): return jsonify({"message": "An account with this email already exists."}), 409
    user = User(name=name, email=email); user.set_password(password)
    db.session.add(user); db.session.commit()
    return jsonify({"message": "Account created.", "user": user.to_dict()}), 201

@auth_bp.post("/login")
def login():
    data = request.get_json(silent=True) or {}
    user = User.query.filter_by(email=data.get("email", "").strip().lower()).first()
    if not user or not user.check_password(data.get("password", "")): return jsonify({"message": "Invalid email or password."}), 401
    return jsonify({"access_token": create_access_token(identity=str(user.id)), "user": user.to_dict()})
