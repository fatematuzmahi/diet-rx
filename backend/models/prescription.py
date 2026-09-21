from backend.extensions import db
class Prescription(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    medicine_name = db.Column(db.String(150), nullable=False)
    notes = db.Column(db.Text)
