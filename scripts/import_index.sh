#!/usr/bin/env bash
set -euo pipefail

if [ $# -lt 1 ]; then
    echo "Usage: $0 <path-to-dump-file>"
    exit 1
fi

DUMP_FILE="$1"
if [ ! -f "$DUMP_FILE" ]; then
    echo "Error: file '$DUMP_FILE' not found."
    exit 1
fi

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

echo "1. Starting PostgreSQL vector container..."
docker compose up -d

echo "2. Waiting for PostgreSQL to become ready..."
until docker exec qwerty-pgvector pg_isready -U qwerty -d qwerty >/dev/null 2>&1; do
    sleep 1
done

echo "3. Initializing database schema (extensions, functions, tables, indexes)..."
uv run python -c "from retrieval.db import init_db; init_db()"

echo "4. Restoring data from $DUMP_FILE..."
docker exec -i qwerty-pgvector pg_restore -U qwerty -d qwerty --clean --if-exists --no-owner --no-privileges < "$DUMP_FILE" || true

echo "5. Verifying restored data counts and embeddings..."
docker exec qwerty-pgvector psql -U qwerty -d qwerty -c "
SELECT 
    (SELECT count(*) FROM documents) AS total_documents,
    (SELECT count(*) FROM chunks) AS total_chunks,
    (SELECT count(*) FROM chunks WHERE embedding IS NULL) AS chunks_null_embeddings,
    (SELECT count(*) FROM lines) AS total_lines,
    (SELECT count(*) FROM lines WHERE embedding IS NULL) AS lines_null_embeddings;
"

echo "Index import and verification completed successfully!"
