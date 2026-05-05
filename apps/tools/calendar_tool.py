import logging
from datetime import date, datetime, time, timedelta
from typing import Optional
from uuid import UUID

from django.utils import timezone

from apps.planner.models import CalendarEvent
from apps.users.models import User

logger = logging.getLogger(__name__)


def get_calendar_events(
    user: User,
    date_from: date,
    date_to: Optional[date] = None,
) -> list[dict]:
    """Fetch calendar events for a date range."""
    if date_to is None:
        date_to = date_from

    events = CalendarEvent.objects.filter(
        user=user,
        start_time__date__gte=date_from,
        start_time__date__lte=date_to,
    ).order_by("start_time")

    result = []
    for e in events:
        result.append({
            "id": str(e.id),
            "title": e.title,
            "event_type": e.event_type,
            "start_time": e.start_time.strftime("%Y-%m-%d %H:%M"),
            "end_time": e.end_time.strftime("%Y-%m-%d %H:%M"),
            "duration_minutes": e.duration_minutes,
            "is_fixed": e.is_fixed,
            "location": e.location,
        })

    logger.info(
        "Fetched %d events for user=%s (%s to %s)",
        len(result), user.username, date_from, date_to,
    )
    return result


def create_calendar_event(
    user: User,
    title: str,
    start_time: datetime,
    end_time: datetime,
    event_type: str = "other",
    is_fixed: bool = False,
    location: str = "",
    source: str = "agent",
) -> dict:
    """Create a new calendar event."""
    event = CalendarEvent.objects.create(
        user=user,
        title=title,
        start_time=start_time,
        end_time=end_time,
        event_type=event_type,
        is_fixed=is_fixed,
        location=location,
        source=source,
    )
    logger.info("Created event '%s' for user=%s", title, user.username)
    return {
        "id": str(event.id),
        "title": event.title,
        "start_time": event.start_time.strftime("%Y-%m-%d %H:%M"),
        "end_time": event.end_time.strftime("%Y-%m-%d %H:%M"),
    }


def move_calendar_event(
    user: User,
    event_id: str,
    new_start: datetime,
    new_end: datetime,
) -> dict:
    """Move an existing event to a new time slot."""
    try:
        event = CalendarEvent.objects.get(id=event_id, user=user)
    except CalendarEvent.DoesNotExist:
        logger.warning("Event %s not found for user=%s", event_id, user.username)
        return {"error": f"Event {event_id} not found"}

    if event.is_fixed:
        logger.warning("Attempted to move fixed event %s", event_id)
        return {"error": f"Event '{event.title}' is fixed and cannot be moved"}

    old_start = event.start_time.strftime("%H:%M")
    event.start_time = new_start
    event.end_time = new_end
    event.save()

    logger.info(
        "Moved event '%s' from %s to %s",
        event.title, old_start, new_start.strftime("%H:%M"),
    )
    return {
        "id": str(event.id),
        "title": event.title,
        "new_start": event.start_time.strftime("%Y-%m-%d %H:%M"),
        "new_end": event.end_time.strftime("%Y-%m-%d %H:%M"),
    }


def delete_calendar_event(user: User, event_id: str) -> dict:
    """Delete a calendar event."""
    try:
        event = CalendarEvent.objects.get(id=event_id, user=user)
    except CalendarEvent.DoesNotExist:
        return {"error": f"Event {event_id} not found"}

    title = event.title
    event.delete()
    logger.info("Deleted event '%s' for user=%s", title, user.username)
    return {"deleted": True, "title": title}