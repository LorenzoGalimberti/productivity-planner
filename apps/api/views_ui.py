"""
Views for the HTML frontend (Django templates).
Separate from the JSON API views.
"""

from django.contrib.auth import authenticate, login, logout
from django.shortcuts import redirect, render
from django.http import HttpRequest, HttpResponse


def dashboard_view(request: HttpRequest) -> HttpResponse:
    """Main dashboard page."""
    if not request.user.is_authenticated:
        return redirect("/login/")
    return render(request, "dashboard.html")


def login_view(request: HttpRequest) -> HttpResponse:
    """Login page."""
    if request.user.is_authenticated:
        return redirect("/")

    error = ""
    if request.method == "POST":
        username = request.POST.get("username", "")
        password = request.POST.get("password", "")
        user = authenticate(request, username=username, password=password)
        if user is not None:
            login(request, user)
            return redirect("/")
        else:
            error = "Invalid username or password."

    return render(request, "login.html", {"error": error})


def logout_view(request: HttpRequest) -> HttpResponse:
    """Logout and redirect to login."""
    logout(request)
    return redirect("/login/")
