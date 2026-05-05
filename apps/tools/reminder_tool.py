"""
Reminder tool — mock implementation.
Designed to be swapped with email / push notification API later.
"""

import logging
from datetime import datetime

from apps.users.models import User

logger = logging.getLogger(__name__)

# In-memory store for mock reminders (replace with real service later)
_pending_reminders: list[dict] = []


def send_reminder(
    user: User,
    message: str,
    remind_at: datetime | None = None,
) -> dict:
    """Send or schedule a reminder (mock)."""
    reminder = {
        "user": user.username,
        "message": message,
        "remind_at": remind_at.isoformat() if remind_at else "now",
        "status": "sent",
    }
    _pending_reminders.append(reminder)

    logger.info(
        "Reminder for user=%s: '%s' at %s",
        user.username, message, reminder["remind_at"],
    )
    return reminder


def get_pending_reminders(user: User) -> list[dict]:
    """Get all pending reminders for a user (mock)."""
    return [r for r in _pending_reminders if r["user"] == user.username]


def clear_reminders(user: User) -> dict:
    """Clear all reminders for a user (mock)."""
    global _pending_reminders
    count = len([r for r in _pending_reminders if r["user"] == user.username])
    _pending_reminders = [r for r in _pending_reminders if r["user"] != user.username]
    return {"cleared": count}