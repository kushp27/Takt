from django.contrib.auth.decorators import login_required
from django.shortcuts import render


@login_required
def home(request):
    """Scaffold placeholder. Lane D replaces this with per-role default screens (D21)."""
    return render(request, "web/home.html")
