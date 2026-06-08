#!/usr/bin/env python3
"""
femisagent_outcome_logger.py — Tier 4 prep.

Snapshots the current scan's per-ticker signals into Supabase
femisagent_signal_outcomes. Forward returns (price_t5d, t20d, t60d) start as
NULL and are filled in by femisagent_outcome_resolver.py running nightly.

After 4-6 weeks of data, cohort analysis becomes possible:
  - "Tickers that fired CEYHUN_OBOB_SELL alone vs CEYHUN_OBOB_SELL + TTR_SELL"
  - "Win rate on PARABOLIC_BLOCK by held vs watchlist context"
  - "Did MULTI_CONFLUENCE_3 predict +20d return better than single flags?"

These cohort discoveries feed back into compute_flags() as new compound flag
definitions, evolving the system beyond the backtest's static flag set.

Called from femisagent_loop.sh after each scan.
"""
import os
import sys
import json
import logging
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

LAST_RUN_PATH = Path("/data/.openclaw/workspace/memory/femisagent_last_run.json")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("outcome_logger")


def fetch_calibration_id():
    """Pull latest calibration ID so we can attribute outcomes to which model
    was making the predictions. Lets us measure id=14 vs id=15 going forward."""
    supa_url = os.environ.get("SUPABASE_URL")
    supa_key = os.environ.get("SUPABASE_KEY")
    if not (supa_url and supa_key):
        return None
    try:
        url = (
            f"{supa_url}/rest/v1/femisapien_backtest_runs"
            f"?select=id&order=run_date.desc&limit=1"
        )
        req = urllib.request.Request(
            url,
            headers={
                "apikey": supa_key,
                "Authorization": f"Bearer {supa_key}",
            },
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.loads(r.read())
            return data[0]["id"] if data else None
    except Exception as e:
        log.warning(f"Could not fetch calibration id: {e}")
        return None


def post_outcomes(outcomes):
    """Bulk insert into femisagent_signal_outcomes."""
    supa_url = os.environ.get("SUPABASE_URL")
    supa_key = os.environ.get("SUPABASE_KEY")
    if not (supa_url and supa_key):
        log.error("SUPABASE_URL/SUPABASE_KEY not set")
        return False
    url = f"{supa_url}/rest/v1/femisagent_signal_outcomes"
    req = urllib.request.Request(
        url,
        data=json.dumps(outcomes).encode(),
        headers={
            "apikey": supa_key,
            "Authorization": f"Bearer {supa_key}",
            "Content-Type": "application/json",
            "Prefer": "return=minimal",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status in (200, 201, 204)
    except Exception as e:
        log.error(f"Supabase insert failed: {e}")
        return False


def get_verdict_tier(r):
    v = r.get("verdict", "") or ""
    for tier in ("EXECUTE", "BUY", "WATCH", "AVOID", "SKIP"):
        if tier in v:
            return tier
    return "WATCH"


def main():
    if not LAST_RUN_PATH.exists():
        log.warning("No femisagent_last_run.json — nothing to log")
        return 0
    with LAST_RUN_PATH.open() as f:
        run = json.load(f)

    scan_ts = run.get("scan_ts") or datetime.now(timezone.utc).isoformat()
    calibration_id = fetch_calibration_id()
    results = run.get("results", [])

    outcomes = []
    for r in results:
        price = r.get("price") or r.get("current_price")
        if price is None or price == 0:
            continue  # skip ETFs/symbols with no price
        outcomes.append({
            "scan_ts": scan_ts,
            "ticker": r["symbol"],
            "price_t0": float(price),
            "verdict_tier": get_verdict_tier(r),
            "ev_score": float(r.get("ev_score", 0) or 0),
            "flags": r.get("all_flags", []),
            "is_held": bool(r.get("qty")),
            "qty": float(r["qty"]) if r.get("qty") else None,
            "cost_basis": float(r["cost_basis"]) if r.get("cost_basis") else None,
            "calibration_id": calibration_id,
        })

    if not outcomes:
        log.info("No valid outcomes to log")
        return 0

    ok = post_outcomes(outcomes)
    if ok:
        log.info(f"Logged {len(outcomes)} signal outcomes (calibration_id={calibration_id})")
    else:
        log.error("Failed to log outcomes")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
