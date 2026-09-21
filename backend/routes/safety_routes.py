from flask import Blueprint, jsonify, request
from backend.services.food_checker import check_food_safety
safety_bp = Blueprint("safety", __name__)
@safety_bp.post("/check")
def safety_check(): return jsonify(check_food_safety(request.get_json(silent=True) or {}))
