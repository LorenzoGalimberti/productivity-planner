from django.urls import path

from . import views

app_name = "api"

urlpatterns = [
    # Health
    path("health/", views.health_check, name="health"),

    # Tasks
    path("tasks/", views.task_list, name="task-list"),
    path("tasks/create/", views.task_create, name="task-create"),
    path("tasks/<str:task_id>/", views.task_update, name="task-update"),
    path("tasks/<str:task_id>/delete/", views.task_delete, name="task-delete"),

    # Calendar Events
    path("events/", views.calendar_event_list, name="event-list"),
    path("events/create/", views.calendar_event_create, name="event-create"),

    # Preferences
    path("preferences/", views.user_preferences, name="preferences"),

    # Memory Notes
    path("memory/", views.memory_notes, name="memory-notes"),

    # Planning (stub — agent wired in Step 5)
    path("plan/day/", views.plan_day, name="plan-day"),
    path("plan/week/", views.plan_week, name="plan-week"),
    path("replan/", views.replan, name="replan"),

    # Planning History
    path("plan/history/", views.planning_history, name="planning-history"),
]