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
         femisagent_chat.py; do
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
         femisagent_outcome_resolver.service femisagent_outcome_resolver.timer; do
  log "Installing $u"
  curl -fsSL --max-time 30 -o "${SYSTEMD_DIR}/${u}" "${REPO_RAW}/${u}"
done

# 3. Reload + enable + start
systemctl daemon-reload

# Don't auto-start chat bot unless ANTHROPIC_API_KEY is configured
if [ -f /etc/femisagent/chat.env ] && grep -q ANTHROPIC_API_KEY /etc/femisagent/chat.env; then
  log "Enabling chat bot (ANTHROPIC_API_KEY configured)"
  systemctl enable --now femisagent_chat.service
else
  log "⚠️  /etc/femisagent/chat.env missing or no ANTHROPIC_API_KEY — chat bot NOT enabled"
  log "    To enable: echo 'ANTHROPIC_API_KEY=sk-ant-...' > /etc/femisagent/chat.env"
  log "    Then: systemctl enable --now femisagent_chat.service"
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
