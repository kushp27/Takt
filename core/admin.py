from django.contrib import admin, messages
from django.utils import timezone

from . import models


MAX_LISTED = 10


def warn_missing_demand(request):
    """Eng review D7 (T5): a missing TaktConfig row raises a red (messages.ERROR) warning in the
    admin, on the TaktConfig list and the admin index. Pace for that family adds 0 units required
    and Line Health names it "no demand set"."""
    from metrics.api import missing_demand

    gaps = missing_demand(include_zero=False)
    if gaps:
        listed = "; ".join(f"{g.line} · {g.family} · {g.production_date:%a %b} {g.production_date.day}"
                           for g in gaps[:MAX_LISTED])
        more = f" and {len(gaps) - MAX_LISTED} more" if len(gaps) > MAX_LISTED else ""
        messages.error(request, f"No demand set: {len(gaps)} scheduled family-day(s) in the last and next 7 "
                                f"days have no demand row, so they add no units required: {listed}{more}.")
    return gaps


_admin_index = admin.site.index


def _index_with_demand_warning(request, extra_context=None):
    warn_missing_demand(request)
    return _admin_index(request, extra_context)


admin.site.index = _index_with_demand_warning


@admin.register(models.Hub)
class HubAdmin(admin.ModelAdmin):
    list_display = ["name", "code", "adapter", "plant_timezone", "quantity_is_gross",
                    "last_successful_ingest_at", "last_new_transaction_at"]
    readonly_fields = ["last_successful_ingest_at", "last_new_transaction_at"]


@admin.register(models.Area)
class AreaAdmin(admin.ModelAdmin):
    list_display = ["name"]


class StationInline(admin.TabularInline):
    model = models.Station
    extra = 0
    fields = ["sequence", "name", "hub_station_code", "station_type", "count_trusted"]
    readonly_fields = ["count_trusted"]


@admin.register(models.Line)
class LineAdmin(admin.ModelAdmin):
    list_display = ["name", "area", "hub"]
    inlines = [StationInline]


@admin.register(models.Station)
class StationAdmin(admin.ModelAdmin):
    list_display = ["name", "line", "sequence", "station_type", "count_trusted", "count_trust_set_at"]
    list_filter = ["line", "station_type", "count_trusted"]
    readonly_fields = ["count_trust_set_by", "count_trust_set_at"]


@admin.register(models.CountTrustChange)
class CountTrustChangeAdmin(admin.ModelAdmin):
    list_display = ["station", "action", "changed_by", "changed_at"]
    list_filter = ["action"]


@admin.register(models.PartFamily)
class PartFamilyAdmin(admin.ModelAdmin):
    list_display = ["name"]


@admin.register(models.Part)
class PartAdmin(admin.ModelAdmin):
    list_display = ["hub_part_code", "family", "hub"]
    list_filter = ["hub", "family"]


@admin.register(models.Routing)
class RoutingAdmin(admin.ModelAdmin):
    list_display = ["family", "station", "visit_count"]
    list_filter = ["family", "station__line"]


@admin.register(models.Operator)
class OperatorAdmin(admin.ModelAdmin):
    list_display = ["name", "hire_date"]
    search_fields = ["name"]


@admin.register(models.OperatorAlias)
class OperatorAliasAdmin(admin.ModelAdmin):
    list_display = ["hub_operator_id", "hub", "operator", "first_seen_at"]
    list_filter = ["hub", ("operator", admin.EmptyFieldListFilter)]
    search_fields = ["hub_operator_id", "operator__name"]
    autocomplete_fields = ["operator"]


class ShiftBreakInline(admin.TabularInline):
    model = models.ShiftBreak
    extra = 0


@admin.register(models.Shift)
class ShiftAdmin(admin.ModelAdmin):
    list_display = ["name", "hub", "start_time", "end_time", "weekdays", "active"]
    inlines = [ShiftBreakInline]


@admin.register(models.NonWorkingDay)
class NonWorkingDayAdmin(admin.ModelAdmin):
    list_display = ["date", "hub", "reason"]


@admin.register(models.TaktConfig)
class TaktConfigAdmin(admin.ModelAdmin):
    list_display = ["production_date", "line", "family", "demand_units"]
    list_filter = ["line", "family"]
    date_hierarchy = "production_date"

    def changelist_view(self, request, extra_context=None):
        warn_missing_demand(request)
        return super().changelist_view(request, extra_context)


@admin.register(models.ScrapCode)
class ScrapCodeAdmin(admin.ModelAdmin):
    list_display = ["code", "description", "hub", "cause_station_hint"]


@admin.register(models.StationPartGrade)
class StationPartGradeAdmin(admin.ModelAdmin):
    list_display = ["station", "part", "grade", "d_share", "c_share", "o_share", "job_count", "days_of_history"]
    list_filter = ["grade", "station__line"]


@admin.register(models.IngestRun)
class IngestRunAdmin(admin.ModelAdmin):
    list_display = ["hub", "mode", "started_at", "ok", "rows_read", "rows_changed", "open_jobs_reread", "rows_skipped", "query_seconds"]
    list_filter = ["hub", "mode", "ok"]


@admin.register(models.SiteSettings)
class SiteSettingsAdmin(admin.ModelAdmin):
    # Gate 1 (HR sign-off) is flipped only by the builder (superuser); the plant admin
    # group keeps the thresholds. Who and when are stamped, never typed.
    GATE1_STAMPS = ["gate1_approved_by", "gate1_approved_at"]

    def get_readonly_fields(self, request, obj=None):
        fields = [*super().get_readonly_fields(request, obj), *self.GATE1_STAMPS]
        if not request.user.is_superuser:
            fields.append("gate1_approved")
        return fields

    def save_model(self, request, obj, form, change):
        if "gate1_approved" in form.changed_data:
            obj.gate1_approved_by = request.user.get_username()
            obj.gate1_approved_at = timezone.now()
        super().save_model(request, obj, form, change)

    def has_add_permission(self, request):
        return not models.SiteSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False
