#!/usr/bin/env python3
"""
femisagent_outcome_resolver.py — Tier 4 nightly job.

For each row in femisagent_signal_outcomes where forward returns are still
NULL, fetch the close price N trading days after scan_ts and fill in:
  - price_t5d  + ret_t5d_pct
  - price_t20d + ret_t20d_pct
  - price_t60d + ret_t60d_pct

Run via systemd timer at 22:00 UTC (post-close) every weekday.

After 4-6 weeks of accumulated data, can run cohort analyses like:
  SELECT verdict_tier,
         COUNT(*) as n,
         AVG(ret_t20d_pct) as avg_ret,
         AVG(CASE WHEN ret_t20d_pct > 0 THEN 1.0 ELSE 0 END) as win_rate
  FROM femisagent_signal_outcomes
  WHERE resolved_t20d = true AND scan_ts > now() - interval '60 days'
  GROUP BY verdict_tier;
"""
import os
import sys
import json
import logging
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

try:
    import yfinance as yf
except ImportError:
    print("yfinance required: pip install yfinance", file=sys.stderr)
    sys.exit(1)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("outcome_resolver")


def supabase_request(method, path, data=None, params=None):
    supa_url = os.environ.get("SUPABASE_URL")
    supa_key = os.environ.get("SUPABASE_KEY")
    if not (supa_url and supa_key):
        raise RuntimeError("SUPABASE_URL/SUPABASE_KEY not set")
    url = f"{supa_url}/rest/v1/{path}"
    if params:
        url += "?" + "&".join(f"{k}={v}" for k, v in params.items())
    headers = {
        "apikey": supa_key,
        "Authorization": f"Bearer {supa_key}",
        "Content-Type": "application/json",
    }
    if method in ("PATCH", "POST"):
        headers["Prefer"] = "return=minimal"
    req = urllib.request.Request(
        url,
        data=json.dumps(data).encode() if data else None,
        headers=headers,
        method=method,
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        body = r.read()
        if r.status >= 300:
            raise RuntimeError(f"HTTP {r.status}: {body[:500]}")
        return json.loads(body) if body else None


def get_unresolved(horizon_days, min_age_days):
    """Fetch rows where the N-day forward return is still NULL and scan_ts is
    old enough (we need at least N trading days to have elapsed)."""
    col = f"resolved_t{horizon_days}d"
    cutoff = (datetime.now(timezone.utc) - timedelta(days=min_age_days)).isoformat()
    rows = supabase_request(
        "GET",
        "femisagent_signal_outcomes",
        params={
            "select": "id,scan_ts,ticker,price_t0",
            col: "eq.false",
            "scan_ts": f"lt.{cutoff}",
            "limit": "500",
            "order": "scan_ts.asc",
        },
    )
    return rows or []


def resolve_horizon(horizon_days, min_calendar_days):
    rows = get_unresolved(horizon_days, min_calendar_days)
    if not rows:
        log.info(f"No unresolved t+{horizon_days}d outcomes")
        return 0

    # Group by ticker to batch yfinance calls
    by_ticker = {}
    for r in rows:
        by_ticker.setdefault(r["ticker"], []).append(r)

    updated = 0
    for ticker, ticker_rows in by_ticker.items():
        try:
            min_scan = min(datetime.fromisoformat(r["scan_ts"].replace("Z", "+00:00")) for r in ticker_rows)
            start = (min_scan - timedelta(days=5)).date()
            end = (datetime.now(timezone.utc) + timedelta(days=1)).date()
            bars = yf.download(ticker, start=start, end=end, progress=False, auto_adjust=False)
            if bars is None or bars.empty:
                log.warning(f"No bars for {ticker}")
                continue
            closes = bars["Close"]
            if hasattr(closes, "columns"):  # yfinance multi-index quirk
                closes = closes.iloc[:, 0]
            for r in ticker_rows:
                scan_date = datetime.fromisoformat(r["scan_ts"].replace("Z", "+00:00")).date()
                # Find closes >= scan_date, take Nth bar
                future = closes[closes.index.date > scan_date]
                if len(future) < horizon_days:
                    continue
                future_price = float(future.iloc[horizon_days - 1])
                t0_price = float(r["price_t0"])
                ret_pct = (future_price / t0_price - 1.0) * 100.0
                update = {
                    f"price_t{horizon_days}d": future_price,
                    f"ret_t{horizon_days}d_pct": round(ret_pct, 4),
                    f"resolved_t{horizon_days}d": True,
                }
                supabase_request(
                    "PATCH",
                    "femisagent_signal_outcomes",
                    data=update,
                    params={"id": f"eq.{r['id']}"},
                )
                updated += 1
        except Exception as e:
            log.warning(f"Failed {ticker}: {e}")

    log.info(f"Resolved {updated} rows at t+{horizon_days}d")
    return updated


def main():
    # Resolve in order: 5d (needs ~7 calendar days), 20d (~28d), 60d (~84d)
    total = 0
    total += resolve_horizon(5, 7)
    total += resolve_horizon(20, 28)
    total += resolve_horizon(60, 84)
    log.info(f"Total resolutions: {total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
