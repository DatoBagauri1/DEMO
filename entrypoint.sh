#!/usr/bin/env bash
set -euo pipefail

python - <<'PY'
import os
import time
import psycopg2

host = os.getenv("POSTGRES_HOST", "postgres")
port = int(os.getenv("POSTGRES_PORT", "5432"))
dbname = os.getenv("POSTGRES_DB", "trippilot")
user = os.getenv("POSTGRES_USER", "trippilot")
password = os.getenv("POSTGRES_PASSWORD", "trippilot")

for _ in range(45):
    try:
        conn = psycopg2.connect(
            dbname=dbname,
            user=user,
            password=password,
            host=host,
            port=port,
        )
        conn.close()
        break
    except psycopg2.OperationalError:
        time.sleep(1)
else:
    raise SystemExit("PostgreSQL is not ready.")
PY

python manage.py migrate --noinput
python manage.py collectstatic --noinput

if [ "${1:-web}" = "web" ]; then
  exec gunicorn trip_pilot.wsgi:application --bind 0.0.0.0:8000 --workers 3 --timeout 120
elif [ "$1" = "worker" ]; then
  exec celery -A trip_pilot worker --loglevel=info --concurrency=4
elif [ "$1" = "beat" ]; then
  exec celery -A trip_pilot beat --loglevel=info
else
  exec "$@"
fi

