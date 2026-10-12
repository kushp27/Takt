"""Disputes (design Component 8, design review D14, eng re-review D4).

One Dispute row per job. A multi-job submission creates N rows sharing a
submission_id and reason; the supervisor decides per job, with "apply to all in
this submission". In-app only: no email (eng review D6).
"""

import uuid

from django.conf import settings
from django.db import models


class Dispute(models.Model):
    WRONG_QUANTITY = "wrong_quantity"
    WRONG_OPERATOR = "wrong_operator"
    NOT_MINE = "not_mine"
    TIME_WRONG = "time_wrong"
    OTHER = "other"
    REASON_CHOICES = [
        (WRONG_QUANTITY, "Wrong quantity"),
        (WRONG_OPERATOR, "Wrong operator"),
        (NOT_MINE, "Job not mine"),
        (TIME_WRONG, "Time looks wrong"),
        (OTHER, "Other"),
    ]

    OPEN = "open"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    STATUS_CHOICES = [(OPEN, "Open"), (CONFIRMED, "Confirmed"), (REJECTED, "Rejected")]

    job = models.ForeignKey("core.Job", on_delete=models.PROTECT, related_name="disputes")
    submission_id = models.UUIDField(default=uuid.uuid4, db_index=True)
    reason = models.CharField(max_length=20, choices=REASON_CHOICES)
    reason_text = models.CharField(max_length=500, blank=True, help_text='Required when reason is "Other".')
    raised_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="disputes_raised")
    raised_at = models.DateTimeField(auto_now_add=True)

    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=OPEN, db_index=True)
    affects_counts = models.BooleanField(
        default=False, help_text="On confirm: the count itself is wrong, so the job also leaves counts."
    )
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="disputes_decided"
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    note = models.TextField(blank=True, help_text="Required for Confirm and Reject; shown back to the lead.")

    class Meta:
        ordering = ["raised_at"]
