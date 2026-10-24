"""SCRUM-51: reminder storage, due-reminder calculation and safety alerts.

Pure functions (no database) decide WHAT should be shown, so they can be unit
tested; the database functions only read and write. Every query filters on the
patient id derived from the verified token.

Safety alerts are deterministic, rule-based and worded as decision support,
never as a diagnosis or emergency instruction (SRS 3.1.3.4.4).
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any, Dict, Iterable, List, Optional

import oracledb

from backend.schemas.notification import DAYS, ReminderIn

REMINDER_COLS = (
    "notification_id, reminder_type, title, message, remind_time, "
    "repeat_days, enabled, created_at, updated_at"
)
EVENT_COLS = (
    "event_id, category, reminder_type, severity, title, message, event_date, "
    "delivered_at, acknowledged_at, ack_action, notification_id"
)

SEVERITY_ORDER = {"HIGH": 0, "CAUTION": 1, "INFO": 2}
_EMPTY_ALLERGY = {"", "none", "no", "nil", "n/a", "na", "nothing", "-"}

DEFAULT_MESSAGES = {
    "MEAL": "Time for your planned meal. Log it in your Daily Log when you're done.",
    "WATER": "Have a glass of water and log it.",
    "MEDICATION": "Time for your medicine, as prescribed by your doctor.",
    "EXERCISE": "Time for your planned activity. Go at a pace that feels safe for you.",
    "GOAL": "Take a moment to check today's goal.",
}

STARTER_REMINDERS = [
    {"reminder_type": "MEAL", "title": "Breakfast", "remind_time": "08:00"},
    {"reminder_type": "WATER", "title": "Drink water", "remind_time": "10:30"},
    {"reminder_type": "MEAL", "title": "Lunch", "remind_time": "13:30"},
    {"reminder_type": "WATER", "title": "Drink water", "remind_time": "16:00"},
    {"reminder_type": "EXERCISE", "title": "Evening walk", "remind_time": "18:00"},
    {"reminder_type": "MEAL", "title": "Dinner", "remind_time": "20:30"},
    {"reminder_type": "GOAL", "title": "Review today's goals", "remind_time": "21:30"},
]


# --------------------------------------------------------------------------- #
# Pure helpers
# --------------------------------------------------------------------------- #
def day_code(day: date) -> str:
    return DAYS[day.weekday()]


def days_to_text(days: Iterable[str]) -> str:
    return ",".join(d for d in DAYS if d in set(days))


def text_to_days(text: Optional[str]) -> List[str]:
    if not text:
        return list(DAYS)
    wanted = {part.strip().upper() for part in str(text).split(",")}
    return [d for d in DAYS if d in wanted] or list(DAYS)


def row_to_reminder(row) -> Dict[str, Any]:
    (nid, rtype, title, message, remind_time, repeat_days, enabled, created, updated) = row
    return {
        "notification_id": int(nid),
        "reminder_type": rtype or "GOAL",
        "title": title or (rtype or "Reminder").title(),
        "message": message,
        "remind_time": remind_time or "09:00",
        "repeat_days": text_to_days(repeat_days),
        "enabled": bool(enabled) if enabled is not None else True,
        "created_at": created,
        "updated_at": updated,
    }


def row_to_event(row) -> Dict[str, Any]:
    (eid, category, rtype, severity, title, message, event_date,
     delivered, acked, action, nid) = row
    if hasattr(event_date, "date"):
        event_date = event_date.date()
    return {
        "event_id": int(eid),
        "category": category,
        "reminder_type": rtype,
        "severity": severity or "INFO",
        "title": title or "Reminder",
        "message": message,
        "event_date": event_date,
        "delivered_at": delivered,
        "acknowledged_at": acked,
        "ack_action": action,
        "notification_id": int(nid) if nid is not None else None,
    }


def due_reminders(reminders: Iterable[Dict[str, Any]], today: date, now_hhmm: str) -> List[Dict[str, Any]]:
    """Enabled reminders scheduled for today whose time has arrived."""
    code = day_code(today)
    due = []
    for r in reminders:
        if not r.get("enabled"):
            continue
        if code not in r.get("repeat_days", []):
            continue
        if (r.get("remind_time") or "99:99") > now_hhmm:
            continue
        due.append({
            "event_key": f"R{r['notification_id']}:{today.isoformat()}",
            "category": "REMINDER",
            "reminder_type": r["reminder_type"],
            "severity": "INFO",
            "title": f"{r['remind_time']} · {r['title']}",
            "message": r.get("message") or DEFAULT_MESSAGES.get(r["reminder_type"], ""),
            "notification_id": r["notification_id"],
        })
    return due


def _singular(word: str) -> str:
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def split_terms(text: Optional[str]) -> List[str]:
    """'Peanuts, tree nuts; Milk' -> ['peanut', 'tree nut', 'milk'] (empty words removed)."""
    terms = []
    for part in re.split(r"[,;/]| and ", str(text or "").lower()):
        part = " ".join(_singular(w) for w in part.strip().split())
        if part and part not in _EMPTY_ALLERGY and part not in terms:
            terms.append(part)
    return terms


def allergy_hits(allergies: Optional[str], meal_title: Optional[str], dish_allergens: Optional[str]) -> List[str]:
    """Allergies that appear in a logged meal's Dish DNA allergens or in its name."""
    wanted = split_terms(allergies)
    if not wanted:
        return []
    allergen_terms = split_terms(dish_allergens)
    title_words = " ".join(_singular(w) for w in re.findall(r"[a-z]+", str(meal_title or "").lower()))
    hits = []
    for allergy in wanted:
        in_dish = any(allergy == a or allergy in a or a in allergy for a in allergen_terms)
        in_name = re.search(rf"\b{re.escape(allergy)}\b", title_words) is not None
        if (in_dish or in_name) and allergy not in hits:
            hits.append(allergy)
    return hits


def safety_alerts(
    today: date,
    missed_meds: Iterable[Dict[str, Any]],
    meals: Iterable[Dict[str, Any]],
    allergies: Optional[str],
    plan_requires_review: bool,
) -> List[Dict[str, Any]]:
    """Rule-based safety alerts for today (SRS 3.1.3.4.3)."""
    key_day = today.isoformat()
    alerts: List[Dict[str, Any]] = []

    for meal in meals:
        hits = allergy_hits(allergies, meal.get("title"), meal.get("allergens"))
        if not hits:
            continue
        names = ", ".join(hits)
        alerts.append({
            "event_key": f"S:ALLERGY:{meal['log_id']}",
            "category": "SAFETY",
            "reminder_type": "MEAL",
            "severity": "HIGH",
            "title": f"Possible allergen in “{meal.get('title') or 'a logged meal'}”",
            "message": (
                f"This meal may contain {names}, which is in your allergy list. "
                "This is an automated check, not a diagnosis. If you feel unwell, "
                "contact your doctor or local emergency services."
            ),
            "notification_id": None,
        })

    for med in missed_meds:
        alerts.append({
            "event_key": f"S:MEDMISS:{med['log_id']}",
            "category": "SAFETY",
            "reminder_type": "MEDICATION",
            "severity": "CAUTION",
            "title": f"Missed medicine: {med.get('title') or 'medicine'}",
            "message": (
                "You marked this medicine as missed today. Check your prescription "
                "or ask your doctor or pharmacist what to do. Don't change your dose on your own."
            ),
            "notification_id": None,
        })

    if plan_requires_review:
        alerts.append({
            "event_key": f"S:PLANREVIEW:{key_day}",
            "category": "SAFETY",
            "reminder_type": "MEAL",
            "severity": "CAUTION",
            "title": "Your plan needs professional review",
            "message": (
                "Some parts of your diet plan have unresolved conflicts. Please check "
                "with your doctor or nutritionist before following those parts."
            ),
            "notification_id": None,
        })
    return alerts


def sort_feed(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Unread first, then most severe, then newest."""
    def key(item):
        delivered = item.get("delivered_at")
        stamp = delivered.timestamp() if hasattr(delivered, "timestamp") else 0
        return (item.get("acknowledged_at") is not None, SEVERITY_ORDER.get(item.get("severity"), 3), -stamp)
    return sorted(items, key=key)


def build_stats(rows, days: int) -> Dict[str, Any]:
    """rows: (category, reminder_type, ack_action)."""
    stats = {"days": days, "delivered": 0, "done": 0, "dismissed": 0, "pending": 0,
             "done_rate": 0, "by_type": {}, "safety_alerts": 0}
    for category, rtype, action in rows:
        stats["delivered"] += 1
        if category == "SAFETY":
            stats["safety_alerts"] += 1
        bucket = stats["by_type"].setdefault(rtype or "OTHER", {"delivered": 0, "done": 0})
        bucket["delivered"] += 1
        if action == "DONE":
            stats["done"] += 1
            bucket["done"] += 1
        elif action == "DISMISSED":
            stats["dismissed"] += 1
        else:
            stats["pending"] += 1
    if stats["delivered"]:
        stats["done_rate"] = round(stats["done"] * 100 / stats["delivered"])
    return stats

# --------------------------------------------------------------------------- #
# Database: reminders
# --------------------------------------------------------------------------- #
def list_reminders(cursor, patient_id: int) -> List[Dict[str, Any]]:
    cursor.execute(
        f"SELECT {REMINDER_COLS} FROM notification WHERE patient_id = :patient_id "
        "ORDER BY remind_time NULLS LAST, notification_id",
        patient_id=patient_id,
    )
    return [row_to_reminder(r) for r in cursor.fetchall()]


def read_reminder(cursor, patient_id: int, notification_id: int) -> Optional[Dict[str, Any]]:
    cursor.execute(
        f"SELECT {REMINDER_COLS} FROM notification "
        "WHERE notification_id = :notification_id AND patient_id = :patient_id",
        notification_id=notification_id,
        patient_id=patient_id,
    )
    row = cursor.fetchone()
    return row_to_reminder(row) if row else None


def count_reminders(cursor, patient_id: int) -> int:
    cursor.execute("SELECT COUNT(*) FROM notification WHERE patient_id = :patient_id", patient_id=patient_id)
    return int(cursor.fetchone()[0])


def insert_reminder(cursor, patient_id: int, reminder: ReminderIn) -> int:
    new_id = cursor.var(int)
    cursor.execute(
        """
        INSERT INTO notification (
            patient_id, reminder_type, title, message, remind_time, repeat_days, enabled
        ) VALUES (
            :patient_id, :reminder_type, :title, :message, :remind_time, :repeat_days, :is_enabled
        )
        RETURNING notification_id INTO :new_id
        """,
        patient_id=patient_id,
        reminder_type=reminder.reminder_type,
        title=reminder.title,
        message=reminder.message,
        remind_time=reminder.remind_time,
        repeat_days=days_to_text(reminder.repeat_days),
        is_enabled=1 if reminder.enabled else 0,
        new_id=new_id,
    )
    value = new_id.getvalue()
    if isinstance(value, (list, tuple)):
        value = value[0]
    return int(value)


def update_reminder(cursor, patient_id: int, notification_id: int, reminder: ReminderIn) -> int:
    cursor.execute(
        """
        UPDATE notification SET
            reminder_type = :reminder_type,
            title = :title,
            message = :message,
            remind_time = :remind_time,
            repeat_days = :repeat_days,
            enabled = :is_enabled,
            updated_at = CURRENT_TIMESTAMP
        WHERE notification_id = :notification_id AND patient_id = :patient_id
        """,
        reminder_type=reminder.reminder_type,
        title=reminder.title,
        message=reminder.message,
        remind_time=reminder.remind_time,
        repeat_days=days_to_text(reminder.repeat_days),
        is_enabled=1 if reminder.enabled else 0,
        notification_id=notification_id,
        patient_id=patient_id,
    )
    return cursor.rowcount or 0


def delete_reminder(cursor, patient_id: int, notification_id: int) -> int:
    cursor.execute(
        "DELETE FROM notification WHERE notification_id = :notification_id AND patient_id = :patient_id",
        notification_id=notification_id,
        patient_id=patient_id,
    )
    return cursor.rowcount or 0


# --------------------------------------------------------------------------- #
# Database: inputs for safety alerts
# --------------------------------------------------------------------------- #
def read_missed_meds(cursor, patient_id: int, today: date) -> List[Dict[str, Any]]:
    try:
        cursor.execute(
            "SELECT log_id, title FROM daily_log WHERE patient_id = :patient_id "
            "AND log_date = :log_date AND log_type = 'MEDICATION' AND status = 'MISSED'",
            patient_id=patient_id,
            log_date=today,
        )
    except oracledb.DatabaseError:  # DAILY_LOG not set up yet
        return []
    return [{"log_id": int(r[0]), "title": r[1]} for r in cursor.fetchall()]


def read_today_meals(cursor, patient_id: int, today: date) -> List[Dict[str, Any]]:
    try:
        cursor.execute(
            "SELECT l.log_id, l.title, d.allergens FROM daily_log l "
            "LEFT JOIN dish_dna d ON d.dish_id = l.dish_id "
            "WHERE l.patient_id = :patient_id AND l.log_date = :log_date AND l.log_type = 'MEAL'",
            patient_id=patient_id,
            log_date=today,
        )
    except oracledb.DatabaseError:
        return []
    return [{"log_id": int(r[0]), "title": r[1], "allergens": r[2]} for r in cursor.fetchall()]


def read_allergies(cursor, patient_id: int) -> Optional[str]:
    cursor.execute("SELECT allergies FROM health_profile WHERE patient_id = :patient_id", patient_id=patient_id)
    row = cursor.fetchone()
    return row[0] if row else None


def read_plan_requires_review(cursor, patient_id: int) -> bool:
    try:
        cursor.execute(
            "SELECT requires_review FROM diet_plan WHERE patient_id = :patient_id "
            "AND status = 'CURRENT' ORDER BY created_at DESC, plan_id DESC FETCH FIRST 1 ROWS ONLY",
            patient_id=patient_id,
        )
    except oracledb.DatabaseError:  # database without the SCRUM-48 columns
        return False
    row = cursor.fetchone()
    return bool(row and row[0])


# --------------------------------------------------------------------------- #
# Database: events (delivery + acknowledgement, SRS 3.1.3.4.5)
# --------------------------------------------------------------------------- #
def record_event(cursor, patient_id: int, today: date, event: Dict[str, Any]) -> None:
    """Record an event once; showing it again later does not duplicate it."""
    cursor.execute(
        """
        MERGE INTO notification_event e
        USING (SELECT :patient_id AS pid, :event_key AS ekey FROM dual) s
        ON (e.patient_id = s.pid AND e.event_key = s.ekey)
        WHEN NOT MATCHED THEN INSERT (
            patient_id, notification_id, event_key, category, reminder_type,
            severity, title, message, event_date
        ) VALUES (
            :patient_id, :notification_id, :event_key, :category, :reminder_type,
            :severity, :title, :message, :event_date
        )
        """,
        patient_id=patient_id,
        notification_id=event.get("notification_id"),
        event_key=event["event_key"],
        category=event["category"],
        reminder_type=event.get("reminder_type"),
        severity=event.get("severity", "INFO"),
        title=(event.get("title") or "")[:150],
        message=(event.get("message") or "")[:500],
        event_date=today,
    )


def read_events_for_day(cursor, patient_id: int, today: date) -> List[Dict[str, Any]]:
    cursor.execute(
        f"SELECT {EVENT_COLS} FROM notification_event "
        "WHERE patient_id = :patient_id AND event_date = :event_date",
        patient_id=patient_id,
        event_date=today,
    )
    return [row_to_event(r) for r in cursor.fetchall()]


def acknowledge_event(cursor, patient_id: int, event_id: int, action: str) -> int:
    cursor.execute(
        "UPDATE notification_event SET acknowledged_at = CURRENT_TIMESTAMP, ack_action = :ack_action "
        "WHERE event_id = :event_id AND patient_id = :patient_id",
        ack_action=action,
        event_id=event_id,
        patient_id=patient_id,
    )
    return cursor.rowcount or 0


def read_event_rows_since(cursor, patient_id: int, start: date):
    cursor.execute(
        "SELECT category, reminder_type, ack_action FROM notification_event "
        "WHERE patient_id = :patient_id AND event_date >= :start_date",
        patient_id=patient_id,
        start_date=start,
    )
    return cursor.fetchall()


def stats_start(today: date, days: int) -> date:
    return today - timedelta(days=days - 1)