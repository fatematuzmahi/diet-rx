from flask import Blueprint, jsonify
grocery_bp = Blueprint("grocery", __name__)
@grocery_bp.get("/list")
def grocery_list(): return jsonify({"message": "Grocery-list generation will be available here.", "items": []})
