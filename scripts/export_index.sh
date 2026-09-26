#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
EXPORT_DIR="$ROOT_DIR/data/export"
DATE_STR="$(date +%Y-%m-%d)"
DUMP_FILE="$EXPORT_DIR/index-${DATE_STR}.dump"

mkdir -p "$EXPORT_DIR"

echo "Exporting documents, chunks, and lines tables from PostgreSQL..."
docker exec qwerty-pgvector pg_dump -U qwerty -d qwerty -Fc -t documents -t chunks -t lines > "$DUMP_FILE"

FILE_SIZE=$(du -h "$DUMP_FILE" | cut -f1)
echo "Index exported successfully to: $DUMP_FILE"
echo "Dump file size: $FILE_SIZE"
