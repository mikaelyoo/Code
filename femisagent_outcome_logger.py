#!/usr/bin/env python3
"""
femisagent_outcome_logger.py — Tier 4: snapshot the latest scan into Supabase
femisagent_signal_outcomes, ONE ROW PER TICKER PER TRADING DAY.

v2 (2026-09-13):
  - Reads the JSON femisagent.py actually writes: {"run_date", "signals": [...]}.
    v1 read "scan_ts"/"results" and logged nothing until hot-patched on the box.
  - Self-loads /etc/femisagent/mcp.env when SUPABASE_* are absent (the systemd
    EnvironmentFile= was silently failing, so cron-launched runs had no creds).
  - Dedupes: the 15-min cron appended ~26 rows per ticker per day (58k rows in
    69 days). Now, if a row already exists for (ticker, today) it is PATCHed
    with the latest scan; otherwise POSTed. Forward returns are therefore
    computed from the last scan of each day (closest to the close).
  - cost_basis reads femisagent's "avg_cost" key (v1 read a key that never existed).

Forward returns (t+5/20/60d) are filled by femisagent_outcome_resolver.py.
"""
import json
import logging
import os
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

LAST_RUN_PATH = Path(os.environ.get(
    "FEMISA_LAST_RUN_PATH",
    "/data/.openclaw/workspace/memory/femisagent_last_run.json"))
ENV_FILE = "/etc/femisagent/mcp.env"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("outcome_logger")


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


def _req(method, path, data=None, params=None, prefer=None):
    base = os.environ["SUPABASE_URL"].rstrip("/")
    key = os.environ["SUPABASE_KEY"]
    url = f"{base}/rest/v1/{path}"
    if params:
        url += "?" + "&".join(f"{k}={v}" for k, v in params.items())
    headers = {"apikey": key, "Authorization": f"Bearer {key}",
               "Content-Type": "application/json"}
    if prefer:
        headers["Prefer"] = prefer
    req = urllib.request.Request(
        url, data=json.dumps(data).encode() if data is not None else None,
        headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=20) as r:
        body = r.read()
        return json.loads(body) if body else None


def fetch_calibration_id():
    try:
        rows = _req("GET", "femisapien_backtest_runs",
                    params={"select": "id", "order": "id.desc", "limit": "1"})
        return rows[0]["id"] if rows else None
    except Exception as e:
        log.warning(f"calibration id lookup failed: {e}")
        return None


def get_verdict_tier(r):
    v = r.get("verdict", "") or ""
    for tier in ("EXECUTE", "BUY", "WATCH", "AVOID", "SKIP"):
        if tier in v:
            return tier
    return "WATCH"


def main():
    load_env_file()
    if not (os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_KEY")):
        log.error("SUPABASE_URL/SUPABASE_KEY not set and %s not readable", ENV_FILE)
        return 1
    if not LAST_RUN_PATH.exists():
        log.warning("No %s — nothing to log", LAST_RUN_PATH)
        return 0

    run = json.loads(LAST_RUN_PATH.read_text())
    signals = run.get("signals") or run.get("results") or []
    run_date = run.get("run_date") or run.get("scan_ts")
    scan_dt = datetime.fromisoformat(run_date) if run_date else datetime.now()
    if scan_dt.tzinfo is None:
        scan_dt = scan_dt.replace(tzinfo=timezone.utc)
    scan_ts = scan_dt.isoformat()
    day0 = scan_dt.date().isoformat()
    day1 = (scan_dt.date() + timedelta(days=1)).isoformat()
    calibration_id = fetch_calibration_id()

    # Existing rows for today → ticker -> id (first row wins)
    existing = {}
    try:
        rows = _req("GET", "femisagent_signal_outcomes", params={
            "select": "id,ticker",
            "and": f"(scan_ts.gte.{day0}T00:00:00Z,scan_ts.lt.{day1}T00:00:00Z)",
            "order": "id.asc", "limit": "2000"})
        for r in rows or []:
            existing.setdefault(r["ticker"], r["id"])
    except Exception as e:
        log.warning(f"existing-row lookup failed ({e}); will insert")

    to_post, patched, skipped = [], 0, 0
    for r in signals:
        price = r.get("price")
        if not price or float(price) <= 0:
            skipped += 1
            continue
        payload = {
            "scan_ts": scan_ts,
            "ticker": r["symbol"],
            "price_t0": float(price),
            "verdict_tier": get_verdict_tier(r),
            "ev_score": float(r.get("ev_score", 0) or 0),
            "flags": r.get("all_flags", []),
            "is_held": bool(r.get("qty")),
            "qty": float(r["qty"]) if r.get("qty") else None,
            "cost_basis": float(r["avg_cost"]) if r.get("avg_cost") else None,
            "calibration_id": calibration_id,
        }
        rid = existing.get(r["symbol"])
        if rid is not None:
            try:
                _req("PATCH", "femisagent_signal_outcomes", data=payload,
                     params={"id": f"eq.{rid}"}, prefer="return=minimal")
                patched += 1
                continue
            except Exception as e:
                log.warning(f"PATCH {r['symbol']} failed ({e}); inserting instead")
        to_post.append(payload)

    posted = 0
    if to_post:
        _req("POST", "femisagent_signal_outcomes", data=to_post, prefer="return=minimal")
        posted = len(to_post)

    log.info(f"Logged {posted + patched} signal outcomes for {day0} "
             f"({posted} new, {patched} updated, {skipped} skipped; "
             f"calibration_id={calibration_id})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
