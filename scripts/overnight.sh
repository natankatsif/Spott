#!/usr/bin/env bash
# Overnight crawling, downloading, parsing, chunking, and indexing pipeline.
# Non-blocking per step: failure in an earlier step does not abort later steps,
# except indexing which requires chunking to succeed.

set -u

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OFFLINE_DIR="$PROJECT_ROOT/offline_indexation"
LOG_DIR="$OFFLINE_DIR/data/logs"
mkdir -p "$LOG_DIR"

log_step() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1"
}

log_step "=== Starting Overnight Pipeline ===" | tee -a "$LOG_DIR/pipeline.log"
cd "$OFFLINE_DIR"

# 1. Crawler (all sites in sites.toml EXCEPT chisinau.md and actelocale.gov.md, max-depth 3)
log_step "Step 1/6: Crawling..." | tee -a "$LOG_DIR/pipeline.log"
ALLOWED_SITES=(
    suburbii.chisinau.md proiecte.chisinau.md mobilitatechisinau.md rtec.md autourban.md
    exdrupo.md dgaurf.md dglca.md autosalubritate.md acc.md agsv.md chisinauedu.dgets.md
    detsriscani.md detsciocana.educ.md detscentru.md buiucanidets.md detsbotanica.md
    educatieonline.md extrascolar.md egradinita.md escoala.chisinau.md dgams.md
    help.chisinau.md amt-botanica.md amt-centru.md amtbuiucani.md amt-ciocana.md
    amtriscani.md botanica.md chisinaucentru.md ciocana.md rascani.md preturabuiucani.md
    comert.chisinau.md visit.chisinau.md invest.chisinau.md e-tineret.md infocom.md liftservice.md
)
uv run python -m crawler --sites "${ALLOWED_SITES[@]}" --max-depth 3 > "$LOG_DIR/crawler.log" 2>&1 || {
    log_step "WARNING: Crawler step failed (exit code $?), continuing..." | tee -a "$LOG_DIR/pipeline.log"
}

# 2. Downloader (new PDFs)
log_step "Step 2/6: Downloading..." | tee -a "$LOG_DIR/pipeline.log"
uv run python -m downloader > "$LOG_DIR/downloader.log" 2>&1 || {
    log_step "WARNING: Downloader step failed (exit code $?), continuing..." | tee -a "$LOG_DIR/pipeline.log"
}

# 3. Parsing (new PDFs via docling)
log_step "Step 3/6: Parsing files..." | tee -a "$LOG_DIR/pipeline.log"
uv run python -m parsing > "$LOG_DIR/parsing.log" 2>&1 || {
    log_step "WARNING: Parsing step failed (exit code $?), continuing..." | tee -a "$LOG_DIR/pipeline.log"
}

# 4. Pages parsing (HTML pages)
log_step "Step 4/6: Parsing pages..." | tee -a "$LOG_DIR/pipeline.log"
uv run python -m pages_parsing > "$LOG_DIR/pages_parsing.log" 2>&1 || {
    log_step "WARNING: Pages parsing step failed (exit code $?), continuing..." | tee -a "$LOG_DIR/pipeline.log"
}

# 5. Chunking (re-chunk all documents)
log_step "Step 5/6: Chunking..." | tee -a "$LOG_DIR/pipeline.log"
CHUNKING_OK=0
if uv run python -m chunking > "$LOG_DIR/chunking.log" 2>&1; then
    CHUNKING_OK=1
    log_step "Chunking completed successfully." | tee -a "$LOG_DIR/pipeline.log"
else
    log_step "ERROR: Chunking step failed (exit code $?). Indexing will be skipped." | tee -a "$LOG_DIR/pipeline.log"
fi

# 6. Indexing (incremental pgvector update only if chunking succeeded)
if [ "$CHUNKING_OK" -eq 1 ]; then
    log_step "Step 6/6: Incremental indexing..." | tee -a "$LOG_DIR/pipeline.log"
    uv run python -m indexing > "$LOG_DIR/indexing.log" 2>&1 || {
        log_step "WARNING: Indexing step failed (exit code $?)." | tee -a "$LOG_DIR/pipeline.log"
    }
else
    log_step "Skipping indexing due to chunking failure." | tee -a "$LOG_DIR/pipeline.log"
fi

# Summary
log_step "Generating corpus stats summary..." | tee -a "$LOG_DIR/pipeline.log"
uv run python scripts/corpus_stats.py > "$LOG_DIR/summary.txt" 2>&1 || true

log_step "=== Overnight Pipeline Finished ===" | tee -a "$LOG_DIR/pipeline.log"
