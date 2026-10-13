"""The metric layer as SQL views (design Component 3; eng review D8: the metric layer is SQL).

The UI only reads these, through metrics/api.py. Planned available time (breaks, DST,
midnight shifts) is ShiftCalendar arithmetic and lives in one place, core/shifts.py; the
views do the counting, the good-unit rule and the yield inputs.

- metrics_job_fact: one row per COMPLETED job (open jobs never count; Component 1), with
  its line, family, operator and duration. Jobs are stamped by completion time at ingest.
- metrics_station_part_shift: per station, part, production date and shift: jobs and
  quantity counted (excluded_from_counts left out), scrap found there, and good units.
  quantity_is_gross hubs: good = quantity - scrap found at that station for the same part
  and shift, floored at 0 (Component 3, Open Question 1). Yield = good / (good + scrap).
- metrics_operator_shift: per station, part, alias, production date and shift: counted
  jobs and quantity, excluded jobs, and rate inputs (jobs not excluded at all).
- metrics_scrap_code_shift: scrap quantity by station found, code, part, date and shift.

A later change to a view is a new migration that replaces it; never edit this one.
"""

from django.db import migrations

JOB_FACT = """
CREATE VIEW metrics_job_fact AS
SELECT j.id AS job_id,
       j.hub_id,
       j.station_id,
       s.line_id,
       s.station_type,
       j.part_id,
       p.family_id,
       j.alias_id,
       a.operator_id,
       j.shift_id,
       j.production_date,
       j.started_at,
       j.completed_at,
       j.quantity,
       GREATEST(EXTRACT(EPOCH FROM (j.completed_at - j.started_at)), 0)::float8 AS duration_s,
       j.excluded,
       j.excluded_from_counts
FROM core_job j
JOIN core_station s ON s.id = j.station_id
JOIN core_part p ON p.id = j.part_id
JOIN core_operatoralias a ON a.id = j.alias_id
WHERE j.completed_at IS NOT NULL;
"""

STATION_PART_SHIFT = """
CREATE VIEW metrics_station_part_shift AS
WITH jobs AS (
    SELECT station_id, part_id, production_date, COALESCE(shift_id, 0) AS shift_key,
           count(*) AS jobs, sum(quantity) AS quantity
    FROM metrics_job_fact
    WHERE NOT excluded_from_counts
    GROUP BY station_id, part_id, production_date, COALESCE(shift_id, 0)
), scrap AS (
    SELECT station_found_id AS station_id, part_id, production_date, COALESCE(shift_id, 0) AS shift_key,
           sum(quantity) AS scrap
    FROM core_scrapevent
    GROUP BY station_found_id, part_id, production_date, COALESCE(shift_id, 0)
), joined AS (
    SELECT COALESCE(j.station_id, sc.station_id) AS station_id,
           COALESCE(j.part_id, sc.part_id) AS part_id,
           COALESCE(j.production_date, sc.production_date) AS production_date,
           NULLIF(COALESCE(j.shift_key, sc.shift_key), 0) AS shift_id,
           COALESCE(j.jobs, 0) AS jobs,
           COALESCE(j.quantity, 0) AS quantity,
           COALESCE(sc.scrap, 0) AS scrap
    FROM jobs j
    FULL OUTER JOIN scrap sc
      ON j.station_id = sc.station_id AND j.part_id = sc.part_id
     AND j.production_date = sc.production_date AND j.shift_key = sc.shift_key
)
SELECT x.station_id, st.line_id, x.part_id, p.family_id, x.production_date, x.shift_id,
       x.jobs, x.quantity, x.scrap,
       CASE WHEN h.quantity_is_gross THEN GREATEST(x.quantity - x.scrap, 0) ELSE x.quantity END AS good
FROM joined x
JOIN core_station st ON st.id = x.station_id
JOIN core_line l ON l.id = st.line_id
JOIN core_hub h ON h.id = l.hub_id
JOIN core_part p ON p.id = x.part_id;
"""

OPERATOR_SHIFT = """
CREATE VIEW metrics_operator_shift AS
SELECT station_id, line_id, part_id, alias_id, operator_id, production_date, shift_id,
       count(*) FILTER (WHERE NOT excluded_from_counts) AS jobs,
       COALESCE(sum(quantity) FILTER (WHERE NOT excluded_from_counts), 0) AS quantity,
       count(*) FILTER (WHERE excluded OR excluded_from_counts) AS excluded_jobs,
       COALESCE(sum(quantity) FILTER (WHERE NOT excluded AND NOT excluded_from_counts), 0) AS rate_quantity,
       COALESCE(sum(duration_s) FILTER (WHERE NOT excluded AND NOT excluded_from_counts), 0) AS rate_seconds
FROM metrics_job_fact
GROUP BY station_id, line_id, part_id, alias_id, operator_id, production_date, shift_id;
"""

SCRAP_CODE_SHIFT = """
CREATE VIEW metrics_scrap_code_shift AS
SELECT e.station_found_id AS station_id, st.line_id, e.scrap_code_id, e.part_id, e.production_date, e.shift_id,
       sum(e.quantity) AS scrap
FROM core_scrapevent e
JOIN core_station st ON st.id = e.station_found_id
GROUP BY e.station_found_id, st.line_id, e.scrap_code_id, e.part_id, e.production_date, e.shift_id;
"""

DROP = """
DROP VIEW IF EXISTS metrics_scrap_code_shift;
DROP VIEW IF EXISTS metrics_operator_shift;
DROP VIEW IF EXISTS metrics_station_part_shift;
DROP VIEW IF EXISTS metrics_job_fact;
"""


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("core", "0002_ingestrun_skipped"),
    ]

    operations = [
        migrations.RunSQL(JOB_FACT + STATION_PART_SHIFT + OPERATOR_SHIFT + SCRAP_CODE_SHIFT, DROP),
    ]
