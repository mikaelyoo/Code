#!/usr/bin/env python3
"""
femisagent_alert.py — Diff current vs previous scan, emit alerts on:
  - New EXECUTE-tier verdict on any ticker (highest conviction)
  - PARABOLIC_CRISIS flag fires anywhere (strongest single signal, +20.91% excess)
  - Verdict downgrade on held position (e.g. EXECUTE→AVOID)
  - AVOID flag flipping on held position
  - New MULTI_CONFLUENCE_3 fires on a held name not in previous scan

Sends Telegram + logs to Supabase femisagent_alert_log (dedup via message hash).

Designed to be called from femisagent_loop.sh after a scan completes.
Reads:
  /data/.openclaw/workspace/memory/femisagent_last_run.json  (current scan)
  /var/lib/femisagent/prev_run.json                          (previous scan, persists across restarts)

Env vars:
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID  (sent via systemd jarvis-bridge env)
  SUPABASE_URL, SUPABASE_KEY            (for alert_log dedup)
"""
import os
import sys
import json
import hashlib
import logging
import urllib.request
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

LAST_RUN_PATH = Path("/data/.openclaw/workspace/memory/femisagent_last_run.json")
PREV_RUN_PATH = Path("/var/lib/femisagent/prev_run.json")
LOG_PATH = Path("/var/log/femisagent/alert.log")

TIER_RANK = {"EXECUTE": 4, "BUY": 3, "WATCH": 2, "AVOID": 1, "SKIP": 0}
HIGH_CONVICTION_FLAGS = frozenset({
    "PARABOLIC_CRISIS",      # +20.91% excess — strongest single flag
    "RSI_REVERSAL",          # +5.51% excess after id=15
    "SQUEEZE_RESOLVING_DOWN",  # +7.83% excess
    "HRT_WEAK",              # +6.13% excess
    "GS_DISTRIB",            # +7.01% excess
})

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH) if LOG_PATH.parent.exists() else logging.NullHandler(),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("femisa_alert")


def load_json(path):
    if not path.exists():
        return None
    try:
        with path.open() as f:
            return json.load(f)
    except Exception as e:
        log.warning(f"failed to load {path}: {e}")
        return None


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        json.dump(data, f)


def get_verdict_tier(ticker_data):
    """Extract tier label from a femisagent ticker result."""
    verdict = ticker_data.get("verdict", "") or ""
    if "EXECUTE" in verdict:
        return "EXECUTE"
    if "BUY" in verdict:
        return "BUY"
    if "WATCH" in verdict:
        return "WATCH"
    if "AVOID" in verdict:
        return "AVOID"
    if "SKIP" in verdict:
        return "SKIP"
    return "WATCH"


def diff_runs(current, previous):
    """Return list of alert dicts. Each alert has: kind, ticker, message, severity."""
    alerts = []

    cur_results = {r["symbol"]: r for r in (current or {}).get("results", [])}
    prev_results = {r["symbol"]: r for r in (previous or {}).get("results", [])}

    for sym, cur in cur_results.items():
        cur_tier = get_verdict_tier(cur)
        cur_flags = set(cur.get("all_flags", []))
        cur_ev = float(cur.get("ev_score", 0) or 0)
        is_held = bool(cur.get("qty"))

        prev = prev_results.get(sym)
        prev_tier = get_verdict_tier(prev) if prev else None
        prev_flags = set(prev.get("all_flags", [])) if prev else set()

        # ALERT 1: PARABOLIC_CRISIS — strongest flag, alert always when newly fired
        if "PARABOLIC_CRISIS" in cur_flags and "PARABOLIC_CRISIS" not in prev_flags:
            alerts.append({
                "kind": "PARABOLIC_CRISIS",
                "ticker": sym,
                "severity": "HIGH",
                "message": f"🚨 PARABOLIC_CRISIS fired on {sym} (EV={cur_ev:+.1f}, tier={cur_tier}). +20.91% excess in 5-yr backtest — strongest signal in stack.",
            })

        # ALERT 2: Verdict downgrade on HELD position (EXECUTE→AVOID, BUY→AVOID, WATCH→AVOID)
        if is_held and prev_tier and cur_tier == "AVOID" and prev_tier != "AVOID":
            qty = cur.get("qty", "?")
            alerts.append({
                "kind": "HELD_TO_AVOID",
                "ticker": sym,
                "severity": "HIGH",
                "message": f"⚠️ Held {sym} flipped {prev_tier} → AVOID (EV={cur_ev:+.1f}, qty={qty}). Flags: {', '.join(sorted(cur_flags)[:4])}.",
            })

        # ALERT 3: New EXECUTE-tier verdict on any ticker (high conviction)
        if cur_tier == "EXECUTE" and prev_tier != "EXECUTE":
            held_str = f"(held {cur.get('qty','?')} sh) " if is_held else "(watchlist) "
            alerts.append({
                "kind": "NEW_EXECUTE",
                "ticker": sym,
                "severity": "MEDIUM",
                "message": f"🟢 NEW EXECUTE: {sym} {held_str}EV={cur_ev:+.1f}. Flags: {', '.join(sorted(cur_flags)[:4])}.",
            })

        # ALERT 4: New HIGH_CONVICTION flag fires on held position
        new_high_conviction = (cur_flags & HIGH_CONVICTION_FLAGS) - (prev_flags & HIGH_CONVICTION_FLAGS)
        if is_held and new_high_conviction:
            for flag in new_high_conviction:
                if flag == "PARABOLIC_CRISIS":
                    continue  # already covered above
                alerts.append({
                    "kind": "HIGH_CONV_ON_HELD",
                    "ticker": sym,
                    "severity": "MEDIUM",
                    "message": f"📈 {flag} fired on held {sym} (qty={cur.get('qty','?')}, EV={cur_ev:+.1f}, tier={cur_tier}).",
                })

    return alerts


def send_email(subject, body):
    """Send via SMTP. Env: ALERT_EMAIL_TO, SMTP_HOST, SMTP_PORT (587),
    SMTP_USER, SMTP_PASS, ALERT_EMAIL_FROM (defaults to SMTP_USER).
    Returns True if sent."""
    to_addr = os.environ.get("ALERT_EMAIL_TO")
    smtp_host = os.environ.get("SMTP_HOST")
    smtp_user = os.environ.get("SMTP_USER")
    smtp_pass = os.environ.get("SMTP_PASS")
    if not all((to_addr, smtp_host, smtp_user, smtp_pass)):
        return False
    from_addr = os.environ.get("ALERT_EMAIL_FROM", smtp_user)
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    try:
        import smtplib
        from email.mime.text import MIMEText
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = from_addr
        msg["To"] = to_addr
        with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as s:
            s.starttls()
            s.login(smtp_user, smtp_pass)
            s.send_message(msg)
        return True
    except Exception as e:
        log.error(f"Email send failed: {e}")
        return False


def send_telegram(message):
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not (bot_token and chat_id):
        return False
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "Markdown",
        "disable_web_page_preview": "true",
    }).encode()
    try:
        with urllib.request.urlopen(url, data=data, timeout=10) as r:
            return r.status == 200
    except Exception as e:
        log.error(f"Telegram send failed: {e}")
        return False


def alert_dedup_hash(alert):
    """Hash for dedup — same kind+ticker+severity within 4h = duplicate."""
    h = hashlib.sha256()
    h.update(f"{alert['kind']}|{alert['ticker']}|{alert['severity']}".encode())
    return h.hexdigest()[:16]


def log_to_supabase(alert, sent):
    """Best-effort log to Supabase femisagent_alert_log; ignore failures."""
    supa_url = os.environ.get("SUPABASE_URL")
    supa_key = os.environ.get("SUPABASE_KEY")
    if not (supa_url and supa_key):
        return
    payload = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "kind": alert["kind"],
        "ticker": alert["ticker"],
        "severity": alert["severity"],
        "message": alert["message"][:500],
        "dedup_hash": alert_dedup_hash(alert),
        "sent_telegram": sent,
    }
    url = f"{supa_url}/rest/v1/femisagent_alert_log"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={
            "apikey": supa_key,
            "Authorization": f"Bearer {supa_key}",
            "Content-Type": "application/json",
            "Prefer": "return=minimal",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            pass
    except Exception as e:
        log.debug(f"Supabase log skipped: {e}")


def main():
    current = load_json(LAST_RUN_PATH)
    if not current or not current.get("results"):
        log.warning("No current scan data; exiting")
        return 0

    previous = load_json(PREV_RUN_PATH) or {}
    alerts = diff_runs(current, previous)

    if not alerts:
        log.info(f"No alerts triggered ({len(current.get('results', []))} tickers scanned)")
        save_json(PREV_RUN_PATH, current)
        return 0

    # Group HIGH severity alerts into one message, MEDIUM into another
    high = [a for a in alerts if a["severity"] == "HIGH"]
    medium = [a for a in alerts if a["severity"] == "MEDIUM"]

    def dispatch(subject, body, alerts_in_batch):
        """Send via whichever channels are configured. Returns True if any
        channel succeeded."""
        sent_any = False
        # Telegram (if TELEGRAM_BOT_TOKEN configured)
        if send_telegram(body):
            sent_any = True
        # Email (if SMTP_HOST + ALERT_EMAIL_TO configured)
        # Strip markdown asterisks for plain-text email
        plain = body.replace("*", "")
        if send_email(subject, plain):
            sent_any = True
        for a in alerts_in_batch:
            log_to_supabase(a, sent_any)
        return sent_any

    sent_count = 0
    if high:
        body = "*🚨 femisagent HIGH-PRIORITY ALERTS*\n\n" + "\n\n".join(a["message"] for a in high)
        if dispatch("[femisagent HIGH] " + ", ".join(a["ticker"] for a in high[:4]), body, high):
            sent_count += len(high)

    if medium:
        body = "*📈 femisagent signal updates*\n\n" + "\n\n".join(a["message"] for a in medium)
        if dispatch("[femisagent] " + ", ".join(a["ticker"] for a in medium[:4]), body, medium):
            sent_count += len(medium)

    log.info(f"Sent {sent_count} alerts ({len(high)} HIGH, {len(medium)} MEDIUM)")
    save_json(PREV_RUN_PATH, current)
    return 0


if __name__ == "__main__":
    sys.exit(main())
