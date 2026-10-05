#!/usr/bin/env bash
# Thin Mac/Linux wrapper. The logic lives in backend/src/spott/ingest/tools/ (works on Windows too):
#   uv run python -m spott.ingest.tools.pipeline full [--only crawler downloader]
set -euo pipefail
cd "$(dirname "$0")/../backend"
exec uv run python -m spott.ingest.tools.pipeline full "$@"
