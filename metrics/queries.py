"""Reads from the metric views (metrics/migrations/0001_metric_views.py). Raw SQL, no ORM models:
the views are the contract."""

from django.db import connection


def _rows(sql, params):
    with connection.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def station_part_shift(line_ids, date_from, date_to):
    """[(station_id, part_id, family_id, production_date, shift_id, jobs, quantity, scrap, good)]"""
    return _rows(
        """SELECT station_id, part_id, family_id, production_date, shift_id, jobs, quantity, scrap, good
           FROM metrics_station_part_shift
           WHERE line_id = ANY(%s) AND production_date BETWEEN %s AND %s""",
        [list(line_ids), date_from, date_to],
    )


def scrap_codes(line_ids, date_from, date_to):
    """[(station_id, scrap_code_id, part_id, production_date, shift_id, scrap)]"""
    return _rows(
        """SELECT station_id, scrap_code_id, part_id, production_date, shift_id, scrap
           FROM metrics_scrap_code_shift
           WHERE line_id = ANY(%s) AND production_date BETWEEN %s AND %s""",
        [list(line_ids), date_from, date_to],
    )


def operator_shift(station_id, date_from, date_to, part_id=None):
    """[(part_id, alias_id, operator_id, production_date, shift_id, jobs, quantity, excluded_jobs,
    rate_quantity, rate_seconds)]"""
    sql = """SELECT part_id, alias_id, operator_id, production_date, shift_id, jobs, quantity, excluded_jobs,
                    rate_quantity, rate_seconds
             FROM metrics_operator_shift
             WHERE station_id = %s AND production_date BETWEEN %s AND %s"""
    params = [station_id, date_from, date_to]
    if part_id is not None:
        sql += " AND part_id = %s"
        params.append(part_id)
    return _rows(sql, params)


def median_operator_rate(station_id, part_id, date_from, date_to):
    """Median units per hour of mapped operators' shifts on this station-part (operator rate
    baseline, Component 3). Unmapped aliases are excluded from comparisons (Component 2)."""
    rows = _rows(
        """SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY 3600.0 * rate_quantity / rate_seconds)
           FROM (SELECT operator_id, production_date, shift_id,
                        sum(rate_quantity) AS rate_quantity, sum(rate_seconds) AS rate_seconds
                 FROM metrics_operator_shift
                 WHERE station_id = %s AND part_id = %s AND operator_id IS NOT NULL
                   AND production_date BETWEEN %s AND %s
                 GROUP BY operator_id, production_date, shift_id) s
           WHERE rate_seconds > 0""",
        [station_id, part_id, date_from, date_to],
    )
    return rows[0][0] if rows and rows[0][0] is not None else None
