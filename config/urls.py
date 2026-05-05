from django.contrib import admin
from django.urls import include, path

from apps.api.views_ui import dashboard_view, login_view, logout_view

urlpatterns = [
    # Admin
    path("admin/", admin.site.urls),

    # API (JSON)
    path("api/", include("apps.api.urls")),

    # Frontend (HTML)
    path("", dashboard_view, name="dashboard"),
    path("login/", login_view, name="login"),
    path("logout/", logout_view, name="logout"),
]
