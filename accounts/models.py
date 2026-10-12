"""Roles (design Component 7). Pilot: Django local accounts for named pilot users only."""

from django.conf import settings
from django.db import models


class Profile(models.Model):
    OPS_MANAGER = "ops_manager"
    SUPERVISOR = "supervisor"
    LEAD = "lead"
    ADMIN = "admin"
    ROLE_CHOICES = [
        (OPS_MANAGER, "Ops manager (all areas)"),
        (SUPERVISOR, "Supervisor (their areas, all shifts)"),
        (LEAD, "Lead (their area and shift)"),
        (ADMIN, "Admin (configuration and aliases; no operator metrics)"),
    ]

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile")
    role = models.CharField(max_length=16, choices=ROLE_CHOICES)
    areas = models.ManyToManyField("core.Area", blank=True, related_name="profiles")
    shift = models.ForeignKey(
        "core.Shift", null=True, blank=True, on_delete=models.SET_NULL, related_name="leads",
        help_text="Leads only: the shift they run.",
    )

    def __str__(self):
        return f"{self.user.get_username()} ({self.get_role_display()})"
