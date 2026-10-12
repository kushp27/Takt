#!/bin/sh
# Scheduler container: supercronic runs docker/crontab. TZ is the plant timezone,
# so "02:00" in the crontab is 02:00 plant time (eng review D9).
set -e
exec supercronic -passthrough-logs /app/docker/crontab
