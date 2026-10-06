#!/bin/sh
# The api container: prepare the database (schema, index dump, sources, contacts), then serve.
set -e
cd /app/offline_indexation && python -m tools.prepare --dumps /dumps
cd /app/backend && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1 --proxy-headers \
    --forwarded-allow-ips '*'
