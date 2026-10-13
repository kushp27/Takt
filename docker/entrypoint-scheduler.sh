#!/bin/sh
# Scheduler container: supercronic runs docker/crontab. TZ is the plant timezone,
# so "02:00" in the crontab is 02:00 plant time (eng review D9).
set -e
# Absolute path: as PID 1 supercronic forks itself to reap children, and a bare name fails ("Failed to fork exec").
exec /usr/local/bin/supercronic -passthrough-logs /app/docker/crontab
