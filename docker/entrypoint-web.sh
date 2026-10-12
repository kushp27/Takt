#!/bin/sh
# Web container: apply migrations, then serve. No hand-run steps (design Component 9).
set -e
python manage.py migrate --noinput
exec gunicorn takt.wsgi:application --bind 0.0.0.0:8000 --workers "${GUNICORN_WORKERS:-3}" --access-logfile -
