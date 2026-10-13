"""Metric views, second version (review pass).

- metrics_count_event: one row per counted job (completed, not excluded_from_counts) and one per
  scrap event, UNION ALL, each with the instant it happened (`at`: completed_at / occurred_at).
  A past `as_of` filters `at <= as_of`, so a replayed window counts only what had happened by then.
- metrics_station_part_shift: GROUP BY over metrics_count_event, then the good-unit rule
  (quantity_is_gross hubs: good = quantity - scrap at that station, part, date and shift, floored
  at 0). Filters on line_id / station_id / production_date are grouping keys, so Postgres pushes
  them down through the GROUP BY into both UNION ALL branches and their indexes (the first
  version's FULL OUTER JOIN on COALESCEd keys could not be filtered before aggregating).
- metrics_operator_shift is dropped: operator rows are read from metrics_job_fact with the same
  as_of filter (metrics/queries.py).

Jobs are stamped by START time (user decision; ingest.service.stamp_instant), so production_date
and shift_id here are the shift the job started in.
"""

from django.db import migrations

UP = """
DROP VIEW IF EXISTS metrics_operator_shift;
DROP VIEW IF EXISTS metrics_station_part_shift;

CREATE VIEW metrics_count_event AS
SELECT f.station_id, f.line_id, f.part_id, f.family_id, f.production_date, f.shift_id,
       f.completed_at AS at, 1 AS jobs, f.quantity, 0 AS scrap
FROM metrics_job_fact f
WHERE NOT f.excluded_from_counts
UNION ALL
SELECT e.station_found_id, st.line_id, e.part_id, p.family_id, e.production_date, e.shift_id,
       e.occurred_at, 0, 0, e.quantity
FROM core_scrapevent e
JOIN core_station st ON st.id = e.station_found_id
JOIN core_part p ON p.id = e.part_id;

CREATE VIEW metrics_station_part_shift AS
SELECT x.station_id, x.line_id, x.part_id, x.family_id, x.production_date, x.shift_id,
       x.jobs, x.quantity, x.scrap,
       CASE WHEN h.quantity_is_gross THEN GREATEST(x.quantity - x.scrap, 0) ELSE x.quantity END AS good
FROM (
    SELECT station_id, line_id, part_id, family_id, production_date, shift_id,
           sum(jobs)::bigint AS jobs, sum(quantity)::bigint AS quantity, sum(scrap)::bigint AS scrap
    FROM metrics_count_event
    GROUP BY station_id, line_id, part_id, family_id, production_date, shift_id
) x
JOIN core_line l ON l.id = x.line_id
JOIN core_hub h ON h.id = l.hub_id;
"""

DOWN = """
DROP VIEW IF EXISTS metrics_station_part_shift;
DROP VIEW IF EXISTS metrics_count_event;
CREATE VIEW metrics_station_part_shift AS
WITH jobs AS (
    SELECT station_id, part_id, production_date, COALESCE(shift_id, 0) AS shift_key,
           count(*) AS jobs, sum(quantity) AS quantity
    FROM metrics_job_fact WHERE NOT excluded_from_counts
    GROUP BY station_id, part_id, production_date, COALESCE(shift_id, 0)
), scrap AS (
    SELECT station_found_id AS station_id, part_id, production_date, COALESCE(shift_id, 0) AS shift_key,
           sum(quantity) AS scrap
    FROM core_scrapevent GROUP BY station_found_id, part_id, production_date, COALESCE(shift_id, 0)
), joined AS (
    SELECT COALESCE(j.station_id, sc.station_id) AS station_id, COALESCE(j.part_id, sc.part_id) AS part_id,
           COALESCE(j.production_date, sc.production_date) AS production_date,
           NULLIF(COALESCE(j.shift_key, sc.shift_key), 0) AS shift_id,
           COALESCE(j.jobs, 0) AS jobs, COALESCE(j.quantity, 0) AS quantity, COALESCE(sc.scrap, 0) AS scrap
    FROM jobs j FULL OUTER JOIN scrap sc
      ON j.station_id = sc.station_id AND j.part_id = sc.part_id
     AND j.production_date = sc.production_date AND j.shift_key = sc.shift_key
)
SELECT x.station_id, st.line_id, x.part_id, p.family_id, x.production_date, x.shift_id, x.jobs, x.quantity, x.scrap,
       CASE WHEN h.quantity_is_gross THEN GREATEST(x.quantity - x.scrap, 0) ELSE x.quantity END AS good
FROM joined x JOIN core_station st ON st.id = x.station_id JOIN core_line l ON l.id = st.line_id
JOIN core_hub h ON h.id = l.hub_id JOIN core_part p ON p.id = x.part_id;
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


class Migration(migrations.Migration):

    dependencies = [
        ("metrics", "0001_metric_views"),
    ]

    operations = [migrations.RunSQL(UP, DOWN)]
