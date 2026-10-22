from datetime import datetime
from backend.extensions import db
class DietPlan(db.Model):
    __tablename__ = "diet_plan"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    title = db.Column(db.String(150), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    #: SCRUM-49: lifecycle status -- CURRENT or SUPERSEDED. Only one plan
    #: per patient is CURRENT at a time.
    status = db.Column(db.String(20), default="CURRENT", nullable=False)
    #: SCRUM-49: "bengali" or "standard" meal schedule.
    meal_schedule = db.Column(db.String(40))
    #: SCRUM-49: daily calorie target in kcal.
    calorie_target = db.Column(db.Float)
    #: SCRUM-49: generated meals as JSON (array of meal objects).
    meal_plan = db.Column(db.JSON)
    #: SCRUM-49: actual macro breakdown as JSON.
    macros = db.Column(db.JSON)
    #: SCRUM-49: inputs used to generate the plan (profile snapshot).
    profile_snapshot = db.Column(db.JSON)
    #: SCRUM-49: dietary restrictions honoured, as JSON.
    restrictions_applied = db.Column(db.JSON)
    #: SCRUM-49: detected dietary conflicts, as JSON.
    conflict_notes = db.Column(db.JSON)
    #: SCRUM-49: true when a doctor/nutritionist must review the plan.
    requires_review = db.Column(db.Boolean, default=False, nullable=False)
    #: SCRUM-49: last regeneration timestamp.
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
