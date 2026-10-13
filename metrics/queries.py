"""Reads from the metric views (metrics/migrations). Raw SQL, no ORM models: the views are the contract.

`cutoff` (an aware instant or None) is the window's as_of: only jobs completed and scrap found
by then count, so a past as_of replays what had happened by that moment. None = no cutoff.
"""

from django.db import connection

INFINITY = "infinity"


def _rows(sql, params):
    with connection.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


STATION_PART_SHIFT_SQL = """
SELECT x.station_id, x.part_id, x.family_id, x.production_date, x.shift_id, x.jobs, x.quantity, x.scrap,
       CASE WHEN h.quantity_is_gross THEN GREATEST(x.quantity - x.scrap, 0) ELSE x.quantity END AS good
FROM (
    SELECT station_id, line_id, part_id, family_id, production_date, shift_id,
           sum(jobs)::bigint AS jobs, sum(quantity)::bigint AS quantity, sum(scrap)::bigint AS scrap
    FROM metrics_count_event
    WHERE line_id = ANY(%(lines)s) AND production_date BETWEEN %(date_from)s AND %(date_to)s
      AND at <= %(cutoff)s
    GROUP BY station_id, line_id, part_id, family_id, production_date, shift_id
) x
JOIN core_line l ON l.id = x.line_id
JOIN core_hub h ON h.id = l.hub_id
"""


def station_part_shift(line_ids, date_from, date_to, cutoff=None):
    """[(station_id, part_id, family_id, production_date, shift_id, jobs, quantity, scrap, good)].
    Without a cutoff this reads the metrics_station_part_shift view; with one, the same GROUP BY
    over metrics_count_event with `at <= cutoff` (the view cannot take a parameter)."""
    if cutoff is None:
        return _rows(
            """SELECT station_id, part_id, family_id, production_date, shift_id, jobs, quantity, scrap, good
               FROM metrics_station_part_shift
               WHERE line_id = ANY(%s) AND production_date BETWEEN %s AND %s""",
            [list(line_ids), date_from, date_to],
        )
    return _rows(STATION_PART_SHIFT_SQL, {"lines": list(line_ids), "date_from": date_from, "date_to": date_to,
                                          "cutoff": cutoff})


def scrap_codes(line_ids, date_from, date_to, cutoff=None):
    """[(station_id, scrap_code_id, part_id, production_date, shift_id, scrap)]"""
    return _rows(
        """SELECT e.station_found_id, e.scrap_code_id, e.part_id, e.production_date, e.shift_id, sum(e.quantity)
           FROM core_scrapevent e JOIN core_station st ON st.id = e.station_found_id
           WHERE st.line_id = ANY(%s) AND e.production_date BETWEEN %s AND %s AND e.occurred_at <= %s
           GROUP BY e.station_found_id, e.scrap_code_id, e.part_id, e.production_date, e.shift_id""",
        [list(line_ids), date_from, date_to, cutoff or INFINITY],
    )


def operator_shift(station_id, date_from, date_to, part_id=None, cutoff=None):
    """[(part_id, alias_id, operator_id, production_date, shift_id, jobs, quantity, excluded_jobs,
    rate_quantity, rate_seconds)] from metrics_job_fact. Counts leave out excluded_from_counts;
    rate inputs leave out every excluded job."""
    sql = """SELECT part_id, alias_id, operator_id, production_date, shift_id,
                    count(*) FILTER (WHERE NOT excluded_from_counts),
                    COALESCE(sum(quantity) FILTER (WHERE NOT excluded_from_counts), 0),
                    count(*) FILTER (WHERE excluded OR excluded_from_counts),
                    COALESCE(sum(quantity) FILTER (WHERE NOT excluded AND NOT excluded_from_counts), 0),
                    COALESCE(sum(duration_s) FILTER (WHERE NOT excluded AND NOT excluded_from_counts), 0)
             FROM metrics_job_fact
             WHERE station_id = %s AND production_date BETWEEN %s AND %s AND completed_at <= %s"""
    params = [station_id, date_from, date_to, cutoff or INFINITY]
    if part_id is not None:
        sql += " AND part_id = %s"
        params.append(part_id)
    sql += " GROUP BY part_id, alias_id, operator_id, production_date, shift_id"
    return _rows(sql, params)


def median_operator_rate(station_id, part_id, date_from, date_to, cutoff=None):
    """Median units per hour of mapped operators' shifts on this station-part (operator rate
    baseline, Component 3). Unmapped aliases are excluded from comparisons (Component 2)."""
    rows = _rows(
        """SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY 3600.0 * rate_quantity / rate_seconds)
           FROM (SELECT operator_id, production_date, shift_id,
                        sum(quantity) AS rate_quantity, sum(duration_s) AS rate_seconds
                 FROM metrics_job_fact
                 WHERE station_id = %s AND part_id = %s AND operator_id IS NOT NULL
                   AND NOT excluded AND NOT excluded_from_counts
                   AND production_date BETWEEN %s AND %s AND completed_at <= %s
                 GROUP BY operator_id, production_date, shift_id) s
           WHERE rate_seconds > 0""",
        [station_id, part_id, date_from, date_to, cutoff or INFINITY],
    )
    return rows[0][0] if rows and rows[0][0] is not None else None
