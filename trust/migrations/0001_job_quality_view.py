"""trust_job_quality: per completed job, the inputs of the trust grade (design Component 4).

- shift_end: the end of the shift the job is stamped in (jobs are stamped by completion
  time, so this is the shift the job completed in), in UTC; NULL outside any shift.
  A shift belongs to the production date it starts on; one ending at or before its start
  time ends the next day. Local end time -> UTC with the hub's zone (DST-safe).
- same_complete_others: other jobs at the same station with an identical completion timestamp (C).
- same_start_complete_others: other jobs at the station with identical start AND completion
  timestamps (the audit's batch-station suggestion, Component 5).
- overlaps: the job overlaps another job by the same operator, at any station (O). A mapped
  operator is one person across hubs; an unmapped alias is treated as its own person.

Thresholds (10% / 300% of the median, last 30 minutes, 3 others) are SiteSettings values
applied by trust/grades.py, so the view stays parameter-free.
"""

from django.db import migrations

VIEW = """
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

    initial = True

    dependencies = [
        ("metrics", "0001_metric_views"),
    ]

    operations = [
        migrations.RunSQL(VIEW, "DROP VIEW IF EXISTS trust_job_quality;"),
    ]
