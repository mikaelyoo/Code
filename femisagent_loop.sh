#!/usr/bin/env bash
# femisagent_loop.sh — Continuous scan + alert dispatcher (Tier 1 agentic loop).
#
# Called by systemd timer every 15 min during market hours (Sun-Thu 14:30-21:00 UTC).
# Idempotent — skips if scan already ran in last 10 min.

set -euo pipefail

SCRIPTS_DIR="/docker/openclaw-vhii/data/.openclaw/workspace/scripts"
STATE_DIR="/var/lib/femisagent"
LOG_DIR="/var/log/femisagent"
LAST_RUN_MARKER="${STATE_DIR}/last_scan_ts"
MIN_INTERVAL_SEC=600   # don't re-run within 10 min

mkdir -p "$STATE_DIR" "$LOG_DIR"

ts() { date '+%Y-%m-%d %H:%M:%S'; }
log() { echo "[$(ts)] $*" >> "${LOG_DIR}/loop.log"; }

# Skip if scanned recently (idempotency for cron over-firing)
if [ -f "$LAST_RUN_MARKER" ]; then
  last_ts=$(stat -c %Y "$LAST_RUN_MARKER" 2>/dev/null || echo 0)
  now_ts=$(date +%s)
  if [ $((now_ts - last_ts)) -lt $MIN_INTERVAL_SEC ]; then
    log "Skipping — last scan was $((now_ts - last_ts))s ago (< ${MIN_INTERVAL_SEC}s)"
    exit 0
  fi
fi

log "═══ femisagent_loop starting ═══"

# Load bridge env (SUPABASE_*, TELEGRAM_*, JARVIS_*)
if systemctl is-active --quiet jarvis-bridge 2>/dev/null; then
  set -a
  eval "$(systemctl show jarvis-bridge -p Environment --value | tr ' ' '\n')"
  set +a
fi
export PRICE_DECOMP_PATH="${SCRIPTS_DIR}/football_field.py"

# Run scan with --portfolio union (covers held + watchlist)
cd "$SCRIPTS_DIR"
SCAN_LOG="${LOG_DIR}/scan_$(date +%Y%m%d_%H%M).log"
log "Running scan → $SCAN_LOG"

if timeout 1200 python3 -u femisagent.py --portfolio --tickers \
  TSLA NVDA AVGO POET RKLB ASTS PL SATS AMZN TMUS QCOM GOOGL \
  DXYZ ADI LRCX AMAT KLAC AMD TXN MU MRVL ARM INTC SNPS TSM \
  LITE AAOI AEHR NBIS COHR AMKR POWL SMTC FN VPG MP JBL HOOD \
  CRCL CVX XLU HIMS MSFT IBIT CEG PWR MPWR FCX GLW WDC \
  DELL HPE ON \
  > "$SCAN_LOG" 2>&1; then
  log "Scan completed OK"
else
  exit_code=$?
  log "Scan failed/timed out (exit=$exit_code) — running alert dispatcher anyway on partial data"
fi

touch "$LAST_RUN_MARKER"

# Run alert dispatcher (Tier 1)
log "Running alert dispatcher"
if python3 "${SCRIPTS_DIR}/femisagent_alert.py" 2>> "${LOG_DIR}/loop.log"; then
  log "Alert dispatcher completed"
else
  log "Alert dispatcher failed"
fi

# Run outcome logger (Tier 4 prep) — snapshots current signals for future P&L resolution
if [ -f "${SCRIPTS_DIR}/femisagent_outcome_logger.py" ]; then
  log "Running outcome logger"
  python3 "${SCRIPTS_DIR}/femisagent_outcome_logger.py" 2>> "${LOG_DIR}/loop.log" || \
    log "Outcome logger failed"
fi

# Prune old scan logs (keep last 100)
ls -t "${LOG_DIR}"/scan_*.log 2>/dev/null | tail -n +101 | xargs -r rm

log "═══ femisagent_loop done ═══"
