#!/usr/bin/env bash
# femisa_sync.sh — one-command sync + scan + backtest for the Jarvis bridge.
#
# Usage:
#   curl -fsSL "https://raw.githubusercontent.com/mikaelyoo/Code/claude/backtest-femisagent-stocks-bdKjE/femisa_sync.sh" | bash
#   # or:
#   curl -fsSL ".../femisa_sync.sh" -o /tmp/femisa_sync.sh && bash /tmp/femisa_sync.sh [--scan] [--backtest] [--portfolio]
#
# Defaults: pulls latest, deploys, runs --portfolio scan + backtest in parallel.
# Outputs go to /tmp/femisa_v283_full.log and /tmp/femisa_backtest.log.

set -euo pipefail

# ─── CONFIG ─────────────────────────────────────────────────────────────────
REPO_BRANCH="${FEMISA_BRANCH:-claude/backtest-femisagent-stocks-bdKjE}"
REPO_RAW="https://raw.githubusercontent.com/mikaelyoo/Code/${REPO_BRANCH}"
SCRIPTS_DIR="/docker/openclaw-vhii/data/.openclaw/workspace/scripts"
SCAN_LOG="/tmp/femisa_v283_full.log"
BT_LOG="/tmp/femisa_backtest.log"

# Default 72-ticker universe (watchlist ∪ portfolio)
DEFAULT_TICKERS=(TSLA NVDA AVGO POET RKLB ASTS PL SATS AMZN TMUS QCOM GOOGL
                 DXYZ ADI LRCX AMAT KLAC AMD TXN MU MRVL ARM INTC SNPS TSM
                 LITE AAOI AEHR NBIS COHR AMKR POWL SMTC FN VPG MP JBL HOOD
                 CRCL CVX XLU HIMS MSFT IBIT CEG PWR MPWR FCX GLW WDC SNDK
                 DELL HPE ON)

# Flags
DO_SCAN=true
DO_BACKTEST=true
USE_PORTFOLIO=true
WAIT_FOR_FINISH=false

for arg in "$@"; do
  case "$arg" in
    --scan)         DO_SCAN=true;  DO_BACKTEST=false ;;
    --backtest)     DO_BACKTEST=true; DO_SCAN=false ;;
    --portfolio)    USE_PORTFOLIO=true ;;
    --no-portfolio) USE_PORTFOLIO=false ;;
    --wait)         WAIT_FOR_FINISH=true ;;
    -h|--help)      sed -n '2,15p' "$0"; exit 0 ;;
  esac
done

# ─── HELPERS ────────────────────────────────────────────────────────────────
ts() { date '+%H:%M:%S'; }
log() { echo "[$(ts)] $*"; }

fetch_and_deploy() {
  local name="$1" url_path="${2:-$1}"
  log "Fetching $name from ${REPO_BRANCH}"
  local tmp="/tmp/__sync_${name}"
  if ! curl -fsSL --max-time 30 -o "$tmp" "${REPO_RAW}/${url_path}"; then
    log "ERROR: curl failed for $name"
    return 1
  fi
  if ! python3 -c "import ast; ast.parse(open('$tmp').read())"; then
    log "ERROR: syntax check failed for $name"
    return 1
  fi
  local target="${SCRIPTS_DIR}/${name}"
  if [ -f "$target" ]; then
    cp "$target" "${target}.prev_$(date +%Y%m%d_%H%M%S).bak"
  fi
  mv "$tmp" "$target"
  log "Deployed $name → $target"
}

load_bridge_env() {
  if systemctl is-active --quiet jarvis-bridge 2>/dev/null; then
    set -a
    eval "$(systemctl show jarvis-bridge -p Environment --value | tr ' ' '\n')"
    set +a
  fi
  export PRICE_DECOMP_PATH="${SCRIPTS_DIR}/football_field.py"
  if [ -f /etc/femisagent/backtest.env ]; then
    set -a; source /etc/femisagent/backtest.env; set +a
  fi
}

# ─── 1. PULL + DEPLOY ───────────────────────────────────────────────────────
log "══════ femisa_sync starting ══════"
log "Branch: ${REPO_BRANCH}"
log "Scripts dir: ${SCRIPTS_DIR}"

fetch_and_deploy "femisagent.py"
fetch_and_deploy "femisagent_backtest.py"

# ─── 2. LOAD ENV ────────────────────────────────────────────────────────────
load_bridge_env
log "Environment loaded (SUPABASE_KEY set: $([ -n "${SUPABASE_KEY:-}" ] && echo yes || echo no))"

# ─── 3. KICK OFF SCAN ───────────────────────────────────────────────────────
SCAN_PID=""
if $DO_SCAN; then
  cd "$SCRIPTS_DIR"
  rm -f "$SCAN_LOG"
  CMD=(python3 -u femisagent.py)
  $USE_PORTFOLIO && CMD+=(--portfolio)
  CMD+=(--tickers "${DEFAULT_TICKERS[@]}")
  nohup timeout 1200 "${CMD[@]}" > "$SCAN_LOG" 2>&1 &
  SCAN_PID=$!
  log "Scan started (PID $SCAN_PID, log $SCAN_LOG)"
fi

# ─── 4. KICK OFF BACKTEST ───────────────────────────────────────────────────
BT_PID=""
if $DO_BACKTEST; then
  cd "$SCRIPTS_DIR"
  rm -f "$BT_LOG"
  nohup timeout 1800 python3 -u femisagent_backtest.py > "$BT_LOG" 2>&1 &
  BT_PID=$!
  log "Backtest started (PID $BT_PID, log $BT_LOG)"
fi

# ─── 5. STATUS ──────────────────────────────────────────────────────────────
sleep 10
echo ""
echo "════════════════════════════════════════════════════════════════════"
if $DO_SCAN; then
  echo "── Scan progress (PID $SCAN_PID) ──"
  tail -8 "$SCAN_LOG" 2>/dev/null || echo "  (no output yet)"
  echo ""
fi
if $DO_BACKTEST; then
  echo "── Backtest progress (PID $BT_PID) ──"
  tail -8 "$BT_LOG" 2>/dev/null || echo "  (no output yet)"
fi
echo "════════════════════════════════════════════════════════════════════"

if $WAIT_FOR_FINISH; then
  log "Waiting for both jobs to finish (--wait passed)…"
  [ -n "$SCAN_PID" ] && wait "$SCAN_PID" || true
  [ -n "$BT_PID" ]   && wait "$BT_PID"   || true
  echo ""
  echo "══════ FINAL OUTPUT ══════"
  if $DO_SCAN; then
    echo "── Scan SIGNAL REPORT ──"
    sed -n '/SIGNAL REPORT/,/Scanned [0-9]/p' "$SCAN_LOG" | tail -200
  fi
  if $DO_BACKTEST; then
    echo "── Backtest per-flag stats ──"
    grep -A 60 "Per-flag stats" "$BT_LOG" | head -70
    echo ""
    grep -E "demoted|Inserted femisapien" "$BT_LOG"
  fi
else
  echo ""
  log "Jobs running in background. Check with:"
  echo "  tail -f $SCAN_LOG"
  echo "  tail -f $BT_LOG"
  echo ""
  log "Or rerun with --wait to block until both finish."
fi
