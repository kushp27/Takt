"""trust_job_quality, second version (review pass).

Jobs are now stamped by START time, so the stamped shift is not where a job completed. The C
share asks whether a job "completes in the last 30 minutes of a shift": completed_window_end is
the end of the SCHEDULED shift window that contains completed_at (the same rule as
core.shifts.window_at: windows starting on the local completion date or the day before, active
shift, weekday scheduled, not a non-working day, earliest start first). A job started in 1st and
batch-closed at the end of 2nd is caught at 2nd's end. NULL when completed outside every shift.

The window functions (clustered completions, overlaps) move out of the view into the grading
query (trust/grades.py), where they run only over the graded dates plus a 1-day pad.
"""

from django.db import migrations

UP = """
DROP VIEW IF EXISTS trust_job_quality;
CREATE VIEW trust_job_quality AS
SELECT f.*, cw.window_end AS completed_window_end
FROM metrics_job_fact f
JOIN core_hub h ON h.id = f.hub_id
LEFT JOIN LATERAL (
    SELECT w.window_end
    FROM (
        SELECT (d.pd + sh.start_time) AT TIME ZONE h.plant_timezone AS window_start,
               ((d.pd + CASE WHEN sh.end_time <= sh.start_time THEN 1 ELSE 0 END) + sh.end_time)
                   AT TIME ZONE h.plant_timezone AS window_end
        FROM core_shift sh
        CROSS JOIN LATERAL (VALUES ((f.completed_at AT TIME ZONE h.plant_timezone)::date),
                                   ((f.completed_at AT TIME ZONE h.plant_timezone)::date - 1)) d(pd)
        WHERE sh.hub_id = f.hub_id AND sh.active
          AND strpos(sh.weekdays, (extract(isodow FROM d.pd)::int - 1)::text) > 0
          AND NOT EXISTS (SELECT 1 FROM core_nonworkingday n WHERE n.hub_id = f.hub_id AND n.date = d.pd)
    ) w
    WHERE f.completed_at >= w.window_start AND f.completed_at < w.window_end
    ORDER BY w.window_start
    LIMIT 1
) cw ON true;
"""

DOWN = """
DROP VIEW IF EXISTS trust_job_quality;
CREATE VIEW trust_job_quality AS
SELECT f.*,
       CASE WHEN sh.id IS NULL THEN NULL ELSE
           ((f.production_date + CASE WHEN sh.end_time <= sh.start_time THEN 1 ELSE 0 END) + sh.end_time)
           AT TIME ZONE h.plant_timezone
       END AS shift_end,
       (count(*) OVER (PARTITION BY f.station_id, f.completed_at)) - 1 AS same_complete_others,
       (count(*) OVER (PARTITION BY f.station_id, f.started_at, f.completed_at)) - 1 AS same_start_complete_others,
       COALESCE(f.started_at < max(f.completed_at) OVER w_prev, false)
         OR COALESCE(lead(f.started_at) OVER w_next < f.completed_at, false) AS overlaps
FROM metrics_job_fact f
JOIN core_hub h ON h.id = f.hub_id
LEFT JOIN core_shift sh ON sh.id = f.shift_id
WINDOW w_prev AS (PARTITION BY COALESCE('o' || f.operator_id::text, 'a' || f.alias_id::text)
                  ORDER BY f.started_at, f.job_id ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING),
       w_next AS (PARTITION BY COALESCE('o' || f.operator_id::text, 'a' || f.alias_id::text)
                  ORDER BY f.started_at, f.job_id);
"""


class Migration(migrations.Migration):

    dependencies = [
        ("trust", "0001_job_quality_view"),
        ("metrics", "0002_count_event_views"),
    ]

    operations = [migrations.RunSQL(UP, DOWN)]
