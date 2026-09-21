from backend.extensions import db
class Food(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False, unique=True)
    calories = db.Column(db.Float, default=0)
    protein_g = db.Column(db.Float, default=0)
    category = db.Column(db.String(80))
