"""Takt's shared schema (design Component 1).

Both hubs (Cores Hub and Dies Hub) map into these tables from day one. Takt never
writes to the MES; these rows are Takt's own read-only copies plus configuration.

All timestamps are stored in UTC (USE_TZ). Each Hub carries its plant timezone;
ingest converts MES local times with zoneinfo and stamps shift + production_date
on every Job and ScrapEvent (eng review D2).
"""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class Hub(models.Model):
    """One MES source: Cores Hub or Dies Hub."""

    ADAPTER_FIXTURE = "fixture"
    ADAPTER_CORES = "cores"
    ADAPTER_DIES = "dies"
    ADAPTER_CHOICES = [
        (ADAPTER_FIXTURE, "Fixture (synthetic data)"),
        (ADAPTER_CORES, "Cores Hub (real MES, not implemented: Open Question 1)"),
        (ADAPTER_DIES, "Dies Hub (real MES, not implemented: Open Question 1)"),
    ]

    code = models.SlugField(unique=True, help_text='"cores" or "dies"')
    name = models.CharField(max_length=80)
    plant_timezone = models.CharField(
        max_length=64, default="America/Chicago",
        help_text="IANA zone of the MES's local timestamps, e.g. America/Chicago (eng review D2).",
    )
    adapter = models.CharField(max_length=16, choices=ADAPTER_CHOICES, default=ADAPTER_FIXTURE)
    quantity_is_gross = models.BooleanField(
        default=False,
        help_text="True if Job.quantity includes units scrapped at that station (Open Question 1). "
        "Then good units = quantity - scrap found at that station for the same part and shift.",
    )
    # Freshness (eng review D5): written by ingest, read by /healthz and the stale banner.
    last_successful_ingest_at = models.DateTimeField(null=True, blank=True)
    last_new_transaction_at = models.DateTimeField(
        null=True, blank=True,
        help_text="When ingest last saw a new or changed MES transaction.",
    )

    def __str__(self):
        return self.name


class Area(models.Model):
    name = models.CharField(max_length=80, unique=True)

    def __str__(self):
        return self.name


class Line(models.Model):
    area = models.ForeignKey(Area, on_delete=models.PROTECT, related_name="lines")
    hub = models.ForeignKey(Hub, on_delete=models.PROTECT, related_name="lines")
    name = models.CharField(max_length=80, unique=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    @property
    def final_station(self):
        return self.stations.order_by("-sequence").first()


class Station(models.Model):
    FLOW = "flow"
    BATCH = "batch"
    TYPE_CHOICES = [(FLOW, "Flow"), (BATCH, "Batch")]

    line = models.ForeignKey(Line, on_delete=models.PROTECT, related_name="stations")
    hub_station_code = models.CharField(
        max_length=64, help_text="The station identifier as it appears in the hub's transactions."
    )
    name = models.CharField(max_length=80)
    sequence = models.PositiveIntegerField(help_text="Position along the line; the highest is the final station.")
    station_type = models.CharField(max_length=8, choices=TYPE_CHOICES, default=FLOW)

    # Count trust (design Component 4a): who set it, when, with what evidence.
    # Changes are also logged in CountTrustChange.
    count_trusted = models.BooleanField(default=False)
    count_trust_set_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    count_trust_set_at = models.DateTimeField(null=True, blank=True)
    count_trust_evidence = models.TextField(blank=True)

    class Meta:
        ordering = ["line", "sequence"]
        constraints = [
            models.UniqueConstraint(fields=["line", "sequence"], name="station_unique_sequence"),
            models.UniqueConstraint(fields=["line", "hub_station_code"], name="station_unique_code"),
        ]

    def __str__(self):
        return f"{self.line} · {self.name}"


class CountTrustChange(models.Model):
    """Audit trail for Station.count_trusted (set, revoked automatically, revoked manually)."""

    SET = "set"
    AUTO_REVOKE = "auto_revoke"
    MANUAL_REVOKE = "manual_revoke"
    ACTION_CHOICES = [(SET, "Set"), (AUTO_REVOKE, "Revoked automatically"), (MANUAL_REVOKE, "Revoked manually")]

    station = models.ForeignKey(Station, on_delete=models.CASCADE, related_name="count_trust_changes")
    action = models.CharField(max_length=16, choices=ACTION_CHOICES)
    changed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    changed_at = models.DateTimeField(auto_now_add=True)
    evidence = models.TextField(blank=True)

    class Meta:
        ordering = ["-changed_at"]


class PartFamily(models.Model):
    name = models.CharField(max_length=80, unique=True)

    class Meta:
        verbose_name_plural = "part families"
        ordering = ["name"]

    def __str__(self):
        return self.name


class Part(models.Model):
    hub = models.ForeignKey(Hub, on_delete=models.PROTECT, related_name="parts")
    hub_part_code = models.CharField(max_length=64)
    family = models.ForeignKey(PartFamily, on_delete=models.PROTECT, related_name="parts")

    class Meta:
        ordering = ["hub_part_code"]
        constraints = [models.UniqueConstraint(fields=["hub", "hub_part_code"], name="part_unique_code")]

    def __str__(self):
        return self.hub_part_code


class Routing(models.Model):
    """Which part families pass through which station, and how many times (Open Question 3)."""

    family = models.ForeignKey(PartFamily, on_delete=models.CASCADE, related_name="routings")
    station = models.ForeignKey(Station, on_delete=models.CASCADE, related_name="routings")
    visit_count = models.PositiveSmallIntegerField(default=1)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["family", "station"], name="routing_unique")]


class Operator(models.Model):
    """A person. Templates must never render .name directly: use {% operator %} (eng re-review D2)."""

    name = models.CharField(max_length=120)
    hire_date = models.DateField(null=True, blank=True, help_text="From the HR roster (Open Question 7).")

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"Operator #{self.pk}"  # never leak a name through str()


class OperatorAlias(models.Model):
    """A hub's operator ID. operator=NULL means unmapped: it lands in the admin mapping queue."""

    hub = models.ForeignKey(Hub, on_delete=models.PROTECT, related_name="aliases")
    hub_operator_id = models.CharField(max_length=64)
    operator = models.ForeignKey(
        Operator, null=True, blank=True, on_delete=models.SET_NULL, related_name="aliases"
    )
    first_seen_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = "operator aliases"
        constraints = [models.UniqueConstraint(fields=["hub", "hub_operator_id"], name="alias_unique")]

    def __str__(self):
        return f"{self.hub.code}:{self.hub_operator_id}"


class Shift(models.Model):
    """ShiftCalendar: a recurring shift in a hub's plant timezone.

    A shift belongs to the production date it starts on, so 23:00-07:00 is one day.
    """

    hub = models.ForeignKey(Hub, on_delete=models.PROTECT, related_name="shifts")
    name = models.CharField(max_length=40, help_text='e.g. "1st", "2nd", "3rd"')
    start_time = models.TimeField()
    end_time = models.TimeField(help_text="Earlier than start_time means the shift crosses midnight.")
    weekdays = models.CharField(
        max_length=7, default="01234",
        help_text="Days the shift starts on, Monday=0 ... Sunday=6, e.g. 01234 for Mon-Fri.",
    )
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["hub", "start_time"]
        constraints = [models.UniqueConstraint(fields=["hub", "name"], name="shift_unique_name")]

    def __str__(self):
        return f"{self.name} shift ({self.hub.code})"

    @property
    def crosses_midnight(self):
        return self.end_time <= self.start_time

    def runs_on(self, weekday):
        return str(weekday) in self.weekdays

    def clean(self):
        if not self.weekdays or any(c not in "0123456" for c in self.weekdays):
            raise ValidationError({"weekdays": "Use digits 0-6 (Monday=0)."})


class ShiftBreak(models.Model):
    """Breaks and planned maintenance are subtracted from planned available time (premise 5)."""

    BREAK = "break"
    PLANNED_MAINTENANCE = "planned_maintenance"
    KIND_CHOICES = [(BREAK, "Break"), (PLANNED_MAINTENANCE, "Planned maintenance")]

    shift = models.ForeignKey(Shift, on_delete=models.CASCADE, related_name="breaks")
    kind = models.CharField(max_length=24, choices=KIND_CHOICES, default=BREAK)
    start_time = models.TimeField()
    end_time = models.TimeField()

    class Meta:
        ordering = ["shift", "start_time"]


class NonWorkingDay(models.Model):
    """A date on which no shift of this hub is scheduled (holiday, shutdown)."""

    hub = models.ForeignKey(Hub, on_delete=models.CASCADE, related_name="non_working_days")
    date = models.DateField()
    reason = models.CharField(max_length=120, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["hub", "date"], name="nonworking_unique")]


class TaktConfig(models.Model):
    """Customer demand per part family per day at a line (Open Question 2).

    A missing row or demand 0 adds 0 units required and shows "no demand set" (eng review D7).
    """

    line = models.ForeignKey(Line, on_delete=models.CASCADE, related_name="demand")
    family = models.ForeignKey(PartFamily, on_delete=models.CASCADE, related_name="demand")
    production_date = models.DateField()
    demand_units = models.PositiveIntegerField()

    class Meta:
        verbose_name = "takt config (demand)"
        verbose_name_plural = "takt config (demand)"
        constraints = [
            models.UniqueConstraint(fields=["line", "family", "production_date"], name="taktconfig_unique")
        ]


class Job(models.Model):
    """One MES transaction, upserted by (hub, mes_id). completed_at NULL = still open."""

    hub = models.ForeignKey(Hub, on_delete=models.PROTECT, related_name="jobs")
    mes_id = models.CharField(max_length=64)
    alias = models.ForeignKey(OperatorAlias, on_delete=models.PROTECT, related_name="jobs")
    station = models.ForeignKey(Station, on_delete=models.PROTECT, related_name="jobs")
    part = models.ForeignKey(Part, on_delete=models.PROTECT, related_name="jobs")
    started_at = models.DateTimeField()
    completed_at = models.DateTimeField(null=True, blank=True)
    quantity = models.IntegerField()

    # Stamped at ingest (eng review D2); NULL shift = outside any scheduled shift.
    shift = models.ForeignKey(Shift, null=True, blank=True, on_delete=models.SET_NULL, related_name="jobs")
    production_date = models.DateField(null=True, blank=True)

    # Set by a confirmed dispute (Component 8). Takt keeps the exclusion even if the
    # MES record later changes, until a supervisor clears it; ingest never touches these.
    excluded = models.BooleanField(default=False, help_text="Excluded from rates and comparisons.")
    excluded_from_counts = models.BooleanField(default=False, help_text="Also excluded from counts.")
    exclusion_reason = models.CharField(max_length=200, blank=True)
    excluded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    excluded_at = models.DateTimeField(null=True, blank=True)

    first_ingested_at = models.DateTimeField(auto_now_add=True)
    last_ingested_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["hub", "mes_id"], name="job_unique_mes_id")]
        indexes = [
            models.Index(fields=["station", "production_date", "shift"]),
            models.Index(fields=["alias", "production_date"]),
            models.Index(fields=["hub"], condition=models.Q(completed_at__isnull=True), name="job_open_idx"),
        ]


class ScrapCode(models.Model):
    hub = models.ForeignKey(Hub, on_delete=models.PROTECT, related_name="scrap_codes")
    code = models.CharField(max_length=32)
    description = models.CharField(max_length=120, blank=True)
    cause_station_hint = models.ForeignKey(
        Station, null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
        help_text="Static hint for the Line Health sentence. A hint, not attribution.",
    )

    class Meta:
        constraints = [models.UniqueConstraint(fields=["hub", "code"], name="scrapcode_unique")]

    def __str__(self):
        return self.description or self.code


class ScrapEvent(models.Model):
    """Scrap recorded where it was found, by scrap code (premise 3)."""

    hub = models.ForeignKey(Hub, on_delete=models.PROTECT, related_name="scrap_events")
    mes_id = models.CharField(max_length=64)
    occurred_at = models.DateTimeField()
    shift = models.ForeignKey(Shift, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    production_date = models.DateField(null=True, blank=True)
    part = models.ForeignKey(Part, on_delete=models.PROTECT, related_name="scrap_events")
    station_found = models.ForeignKey(Station, on_delete=models.PROTECT, related_name="scrap_events")
    scrap_code = models.ForeignKey(ScrapCode, on_delete=models.PROTECT, related_name="events")
    quantity = models.PositiveIntegerField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["hub", "mes_id"], name="scrap_unique_mes_id")]
        indexes = [models.Index(fields=["station_found", "production_date", "shift"])]


class StationPartGrade(models.Model):
    """Output of the trust-grade job (Component 4), one row per station-and-part.

    The audit is the same job run once over history, so audit and nightly always agree.
    """

    GREEN = "green"
    AMBER = "amber"
    RED = "red"
    NOT_GRADED = "not_graded"
    GRADE_CHOICES = [(GREEN, "Green"), (AMBER, "Amber"), (RED, "Red"), (NOT_GRADED, "Not graded yet")]

    station = models.ForeignKey(Station, on_delete=models.CASCADE, related_name="grades")
    part = models.ForeignKey(Part, on_delete=models.CASCADE, related_name="grades")
    grade = models.CharField(max_length=12, choices=GRADE_CHOICES)
    d_share = models.FloatField(help_text="Durations under the short or over the long threshold of the median.")
    c_share = models.FloatField(help_text="Completes in the last minutes of a shift, or clustered timestamps.")
    o_share = models.FloatField(help_text="Jobs overlapping another job by the same operator.")
    job_count = models.PositiveIntegerField()
    days_of_history = models.PositiveIntegerField()
    computed_at = models.DateTimeField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["station", "part"], name="grade_unique")]


class IngestRun(models.Model):
    """One ingest run per hub: rows read and query time are logged (eng review D9)."""

    INCREMENTAL = "incremental"
    NIGHTLY = "nightly"
    BACKFILL = "backfill"
    MODE_CHOICES = [(INCREMENTAL, "Incremental (48 h)"), (NIGHTLY, "Nightly (7 days)"),
                    (BACKFILL, "Backfill (ingest --days N)")]

    hub = models.ForeignKey(Hub, on_delete=models.CASCADE, related_name="ingest_runs")
    mode = models.CharField(max_length=12, choices=MODE_CHOICES)
    started_at = models.DateTimeField()
    finished_at = models.DateTimeField(null=True, blank=True)
    ok = models.BooleanField(default=False)
    rows_read = models.PositiveIntegerField(default=0)
    rows_changed = models.PositiveIntegerField(default=0)
    open_jobs_reread = models.PositiveIntegerField(default=0)
    lookback_hours = models.FloatField(null=True, blank=True, help_text="How far back this run re-read.")
    query_seconds = models.FloatField(default=0)
    # Rows with a station or part code Takt does not know: logged and counted, never silently dropped.
    rows_skipped = models.PositiveIntegerField(default=0)
    skipped_detail = models.TextField(blank=True, help_text="Unknown codes and how many rows each skipped.")
    error = models.TextField(blank=True)

    class Meta:
        ordering = ["-started_at"]


class SiteSettings(models.Model):
    """Singleton for admin-tunable settings. Use SiteSettings.load()."""

    # Gate 1 (design Build order; eng re-review D2): until HR (and any union or works
    # council) approves, named operator views are visible only to the builder (superuser);
    # everyone else sees per-page letters (D12, eng re-review D3).
    gate1_approved = models.BooleanField(
        default=False,
        help_text="Gate 1: HR has approved operator-level views for leads, supervisors and the ops manager.",
    )
    gate1_approved_by = models.CharField(max_length=120, blank=True)
    gate1_approved_at = models.DateTimeField(null=True, blank=True)

    # Trust grade thresholds (Component 4). Builder tunes after the audit; plant admin owns after handoff.
    grade_green_max_share = models.FloatField(default=0.10, help_text="Green when D, C and O are each under this.")
    grade_red_min_share = models.FloatField(default=0.25, help_text="Red when any of D, C, O is over this.")
    duration_short_ratio = models.FloatField(default=0.10, help_text="D: duration under this × the median.")
    duration_long_ratio = models.FloatField(default=3.00, help_text="D: duration over this × the median.")
    end_of_shift_minutes = models.PositiveIntegerField(default=30, help_text="C: completes in the last N minutes.")
    clustered_timestamp_others = models.PositiveIntegerField(
        default=3, help_text="C: an identical completion timestamp shared with at least N other jobs."
    )
    grade_window_days = models.PositiveIntegerField(default=28, help_text="Trailing window (4 weeks).")

    # Count trust revocation (Component 4a).
    count_dispute_revoke_share = models.FloatField(default=0.05)
    count_dispute_revoke_days = models.PositiveIntegerField(default=14)

    # Operability (eng review D5) and metrics.
    stale_after_minutes = models.PositiveIntegerField(default=30)
    upstream_gap_minutes = models.PositiveIntegerField(default=15)
    tenure_flag_days = models.PositiveIntegerField(default=90)
    # Line Health classification (Component 6): a flat 14-day baseline must not make every
    # wobble "low" or "high". The SD used for the ±1 SD band is at least this many points.
    pace_sd_floor_pts = models.FloatField(default=2.0, help_text="Minimum SD, in percentage points, for pace.")
    yield_sd_floor_pts = models.FloatField(default=0.5, help_text="Minimum SD, in percentage points, for yield.")

    class Meta:
        verbose_name = "site settings"
        verbose_name_plural = "site settings"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def __str__(self):
        return "Site settings"
