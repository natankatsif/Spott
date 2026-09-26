#!/usr/bin/env bash
# scripts/update.sh — single-command corpus update pipeline.
#
# Usage:
#   bash scripts/update.sh              # full update (all sites)
#   bash scripts/update.sh --dry-run    # show what would run without executing
#
# Stages:  crawler --resume → downloader --refresh → parsing (new only)
#          → pages_parsing → indexing → short report
#
# Logs go to data/logs/update-<YYYY-MM-DD>/

set -euo pipefail
cd "$(dirname "$0")/.."      # project root (qwerty/)

DATE=$(date +%Y-%m-%d_%H%M%S)
LOG_DIR="data/logs/update-${DATE}"
mkdir -p "$LOG_DIR"

DRY_RUN=false
if [[ "${1:-}" == "--dry-run" ]]; then
    DRY_RUN=true
fi

OI_DIR="offline_indexation"

run_stage() {
    local stage_name="$1"
    shift
    echo "━━━ $(date '+%H:%M:%S') ▸ ${stage_name} ━━━"
    if $DRY_RUN; then
        echo "  [dry-run] would run: uv run python $*"
        return 0
    fi
    local log_file="${LOG_DIR}/${stage_name}.log"
    local start=$(date +%s)
    (cd "$OI_DIR" && uv run python "$@") 2>&1 | tee "$log_file"
    local end=$(date +%s)
    echo "  ⏱  ${stage_name} finished in $(( end - start ))s"
    echo ""
}

echo "╔══════════════════════════════════════════════════════════╗"
echo "║           Corpus Update Pipeline — ${DATE}           ║"
echo "╠══════════════════════════════════════════════════════════╣"
echo "║  Logs → ${LOG_DIR}                                      ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo ""

# 1. Crawler — resume to pick up where the last crawl stopped
run_stage "crawler" -m crawler --resume

# 2. Downloader — refresh to check for updates + download new
run_stage "downloader" -m downloader --refresh

# 3. Parsing — only new/pending files (no --reparse)
run_stage "parsing" -m parsing

# 4. Pages parsing — only new/changed pages
run_stage "pages_parsing" -m pages_parsing

# 5. Indexing — re-chunk from registry, embed new, remove stale
run_stage "indexing" -m indexing

# 6. Report
echo ""
echo "╔══════════════════════════════════════════════════════════╗"
echo "║                    Update Summary                       ║"
echo "╚══════════════════════════════════════════════════════════╝"

# Parse indexing log for stats
if [[ -f "${LOG_DIR}/indexing.log" ]]; then
    echo ""
    echo "Indexing results:"
    grep -E "(Documents indexed|Chunks:|Chunk emb|Line emb|Stale|Orphans)" "${LOG_DIR}/indexing.log" || true
fi

# Count registry stats
if [[ -f "${OI_DIR}/data/registry.sqlite" ]]; then
    echo ""
    echo "Registry:"
    sqlite3 "${OI_DIR}/data/registry.sqlite" "
        SELECT 'Documents: ' || COUNT(*) || ' total, ' ||
               SUM(CASE WHEN status='downloaded' THEN 1 ELSE 0 END) || ' downloaded, ' ||
               SUM(CASE WHEN status='removed' THEN 1 ELSE 0 END) || ' removed'
        FROM documents;
        SELECT 'Files: ' || COUNT(*) || ' total, ' ||
               SUM(CASE WHEN parse_status='parsed' THEN 1 ELSE 0 END) || ' parsed'
        FROM files;
        SELECT 'Pages: ' || COUNT(*) || ' total, ' ||
               SUM(CASE WHEN parse_status='parsed' THEN 1 ELSE 0 END) || ' parsed'
        FROM pages;
    "
fi

echo ""
echo "✅ Update complete. Logs in ${LOG_DIR}"
