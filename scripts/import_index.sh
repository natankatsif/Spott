#!/usr/bin/env bash
# Thin Mac/Linux wrapper. The logic lives in offline_indexation/tools/ (works on Windows too):
#   uv run python -m tools.index_io import <dump>
set -euo pipefail
DUMP="$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"   # absolute: we cd below
cd "$(dirname "$0")/../offline_indexation"
exec uv run python -m tools.index_io import "$DUMP"
