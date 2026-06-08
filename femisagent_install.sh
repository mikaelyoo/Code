#!/usr/bin/env bash
# femisagent_install.sh — one-shot installer for Tier 1+3+4 agentic stack.
#
# Idempotent: pulls latest from GitHub, deploys to bridge, installs systemd
# units, starts services.
#
# Run on the Hostinger bridge:
#   curl -fsSL "https://raw.githubusercontent.com/mikaelyoo/Code/claude/backtest-femisagent-stocks-bdKjE/femisagent_install.sh" | bash

set -euo pipefail

REPO_BRANCH="${FEMISA_BRANCH:-claude/backtest-femisagent-stocks-bdKjE}"
REPO_RAW="https://raw.githubusercontent.com/mikaelyoo/Code/${REPO_BRANCH}"
SCRIPTS_DIR="/docker/openclaw-vhii/data/.openclaw/workspace/scripts"
SYSTEMD_DIR="/etc/systemd/system"

ts() { date '+%Y-%m-%d %H:%M:%S'; }
log() { echo "[$(ts)] $*"; }

log "════ femisagent agent stack install ════"

# 1. Pull all the new files
for f in femisagent.py femisagent_backtest.py femisa_sync.sh \
         femisagent_alert.py femisagent_loop.sh \
         femisagent_outcome_logger.py femisagent_outcome_resolver.py \
         femisagent_chat.py \
         femisagent_rebalance.py risk_policy.yml.example \
         femisagent_mcp.py; do
  log "Pulling $f"
  if curl -fsSL --max-time 30 -o "/tmp/__inst_$f" "${REPO_RAW}/${f}"; then
    if [[ "$f" == *.py ]]; then
      python3 -c "import ast; ast.parse(open('/tmp/__inst_$f').read())" || { log "Syntax check failed for $f"; exit 1; }
    fi
    mv "/tmp/__inst_$f" "${SCRIPTS_DIR}/${f}"
    [[ "$f" == *.sh ]] && chmod +x "${SCRIPTS_DIR}/${f}"
  else
    log "Failed to fetch $f"
    exit 1
  fi
done

# 2. Pull systemd units
mkdir -p /var/lib/femisagent /var/log/femisagent /etc/femisagent
for u in femisagent_loop.service femisagent_loop.timer \
         femisagent_chat.service \
         femisagent_outcome_resolver.service femisagent_outcome_resolver.timer \
         femisagent_mcp.service; do
  log "Installing $u"
  curl -fsSL --max-time 30 -o "${SYSTEMD_DIR}/${u}" "${REPO_RAW}/${u}"
done

# 3. Seed risk policy template if /etc/femisagent/risk_policy.yml not present
if [ ! -f /etc/femisagent/risk_policy.yml ] && [ -f "${SCRIPTS_DIR}/risk_policy.yml.example" ]; then
  cp "${SCRIPTS_DIR}/risk_policy.yml.example" /etc/femisagent/risk_policy.yml
  log "Seeded /etc/femisagent/risk_policy.yml from example (EDIT THE NAV ESTIMATE)"
fi

# 4. Reload + enable + start
systemctl daemon-reload

# Install MCP server deps (idempotent; uses --upgrade-strategy only-if-needed)
log "Installing MCP server deps (mcp, starlette, uvicorn)"
pip install --quiet --break-system-packages mcp starlette uvicorn 2>&1 | tail -5 || \
  log "⚠️  pip install failed — install manually: pip install mcp starlette uvicorn"

# Enable MCP server (HTTP mode) — listens on localhost:8766 by default.
# Expose externally with nginx/caddy reverse proxy + TLS for claude.ai connector.
log "Enabling MCP server (HTTP, port 8766)"
systemctl enable --now femisagent_mcp.service
sleep 2
if systemctl is-active --quiet femisagent_mcp.service; then
  log "MCP server active: http://localhost:8766/mcp"
else
  log "⚠️  femisagent_mcp.service failed to start — check: journalctl -u femisagent_mcp -n 30"
fi

# Telegram chat bot is now optional (MCP replaces conversational layer).
# Only enable if explicitly configured.
if [ -f /etc/femisagent/chat.env ] && grep -q OPENROUTER_API_KEY /etc/femisagent/chat.env; then
  log "Enabling Telegram chat bot (legacy — MCP is preferred)"
  systemctl enable --now femisagent_chat.service
else
  log "(Telegram chat bot not enabled — MCP server is now the primary chat interface)"
fi

log "Enabling continuous scan timer"
systemctl enable --now femisagent_loop.timer

log "Enabling nightly outcome resolver"
systemctl enable --now femisagent_outcome_resolver.timer

log ""
log "════ install complete ════"
log ""
log "Status check:"
systemctl status femisagent_loop.timer --no-pager --lines=3 || true
systemctl status femisagent_outcome_resolver.timer --no-pager --lines=3 || true
systemctl status femisagent_chat.service --no-pager --lines=3 2>/dev/null || true
log ""
log "Logs:"
log "  /var/log/femisagent/loop.log    (continuous scan)"
log "  /var/log/femisagent/alert.log   (alert dispatcher)"
log "  /var/log/femisagent/chat.log    (Telegram bot)"
log ""
log "Manual triggers:"
log "  systemctl start femisagent_loop.service             # one-shot scan now"
log "  systemctl start femisagent_outcome_resolver.service # one-shot resolve now"
log ""
log "Next 5 scheduled scans:"
systemctl list-timers femisagent_loop.timer --no-pager
