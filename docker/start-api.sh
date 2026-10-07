#!/bin/sh
# The api container: prepare the database (schema, index dump, sources, contacts), then serve.
set -e
cd /app/backend
python -m spott.ingest.tools.prepare --dumps /dumps
exec uvicorn spott.api.main:app --host 0.0.0.0 --port 8000 --workers 1 --proxy-headers \
    --forwarded-allow-ips '*'
