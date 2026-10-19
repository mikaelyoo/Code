#!/usr/bin/env python3
"""
femisagent_outcome_resolver.py — Tier 4 nightly job (v2, 2026-09-13).

Fills price_t5d/t20d/t60d, ret_*_pct and resolved_* on femisagent_signal_outcomes
by calling the Postgres function femisagent_resolve_outcomes(). That function
self-joins the daily price_t0 series the scan itself logs — the (t+N)th later
scan-day close for the same ticker — so no yfinance, no per-row PATCH loop,
one RPC call. Idempotent; safe to run any time.

Why v2: v1 (yfinance + per-row PATCH) never resolved a single row in three
months. Its systemd unit's EnvironmentFile pointed at a file that did not
exist, so SUPABASE_* were unset and the script died on its first request every
night. v2 self-loads /etc/femisagent/mcp.env as a fallback.

Horizon note: "N trading days" == N later scan-days for that ticker (the cron
runs Mon–Fri during market hours). Holidays / missed cron days shift a horizon
by at most a day or two — same approximation as bar-count methods.
"""
import json
import logging
import os
import sys
import urllib.error
import urllib.request

ENV_FILE = "/etc/femisagent/mcp.env"
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("outcome_resolver")


def load_env_file(path=ENV_FILE):
    if os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_KEY"):
        return
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    except FileNotFoundError:
        pass


def main():
    load_env_file()
    base = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_KEY")
    if not (base and key):
        log.error("SUPABASE_URL/SUPABASE_KEY not set and %s not readable", ENV_FILE)
        return 1
    req = urllib.request.Request(
        f"{base}/rest/v1/rpc/femisagent_resolve_outcomes", data=b"{}",
        headers={"apikey": key, "Authorization": f"Bearer {key}",
                 "Content-Type": "application/json", "Accept": "application/json"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            res = json.loads(r.read() or b"[]")
    except urllib.error.HTTPError as e:
        log.error("RPC failed HTTP %s: %s", e.code, e.read()[:500].decode("utf-8", "replace"))
        return 1
    row = res[0] if isinstance(res, list) and res else res
    log.info("Resolved forward returns: %s", row)
    return 0


if __name__ == "__main__":
    sys.exit(main())
