from flask import Blueprint, jsonify
diet_bp = Blueprint("diets", __name__)
@diet_bp.get("/plan")
def get_diet_plan(): return jsonify({"message": "Diet-plan generation will be available here.", "data": []})
