#!/usr/bin/env bash
# Thin Mac/Linux wrapper. The logic lives in offline_indexation/tools/ (works on Windows too):
#   uv run python -m tools.pipeline update [--dry-run]
set -euo pipefail
cd "$(dirname "$0")/../offline_indexation"
exec uv run python -m tools.pipeline update "$@"
