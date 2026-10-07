#!/usr/bin/env bash
# Thin Mac/Linux wrapper. The logic lives in backend/src/spott/ingest/tools/ (works on Windows too):
#   uv run python -m spott.ingest.tools.index_io import <dump>
set -euo pipefail
DUMP="$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"   # absolute: we cd below
cd "$(dirname "$0")/../backend"
exec uv run python -m spott.ingest.tools.index_io import "$DUMP"
