#!/usr/bin/env python3
"""
FEMISAGENT_BACKTEST v1.4 — yfinance-based historical backtest → Supabase.

v1.2: per-bar SPY ret_20d pass-through.
v1.3: --investigate-flag mode.

v1.4: two fixes from the v1.6 dual-regime analysis:
  - Wilson-CI demote rule now requires BOTH `Wilson_lower < threshold`
    AND `excess_ret_pct < 0`. The pure-WR version killed fat-tail
    positive signals like SQUEEZE_BULLISH (54% WR, +30% avg_ret) where
    mediocre WR pairs with huge avg_ret and very positive excess.
  - Pre-fetch SPY 60d return AND 60d-drawdown alongside 20d return.
    Passed per-bar to compute_flags for the v1.7 PARABOLIC_RECOVERY
    flag (gates on SPY drawdown from 60d high).

Pulls daily bars via yfinance for a watchlist over a date range, slides
compute_flags() (imported from femisagent.py) across each ticker's history,
records forward N-trading-day returns per flag fire, aggregates per-flag
{n, win_rate_pct, avg_ret_pct}, optionally applies a Wilson-CI demote
rule, then POSTs the result as a new row in public.femisapien_backtest_runs.

v1.1: also computes the *unconditional* baseline (avg forward N-day return +
win-rate across ALL (i, i+N) windows, not conditional on any flag firing).
Each flag's stats now include excess_ret_pct = avg_ret_pct - baseline_ret_pct
and excess_wr_pct = win_rate_pct - baseline_win_rate_pct. This is the proper
"does the flag actually carry information?" metric — a flag with raw avg_ret
+10% on a universe whose unconditional 60d return is also +10% has zero
edge; only excess matters. The raw avg_ret_pct is preserved in the flag_stats
JSON (which femisagent.py reads), so live scanner behavior is unchanged. The
baseline lives in raw_metadata.

The live femisagent.py v1.1+ picks up the newest row on its next scan
(load_latest_calibration), so the pipeline is: run this → next scan uses
the new calibration. Audit trail lives in Supabase.

Requires: yfinance, SUPABASE_KEY + SUPABASE_URL env vars (service-role
key bypasses RLS; anon key needs a SELECT+INSERT policy).

Usage:
    python3 femisagent_backtest.py                          # default 5y
    python3 femisagent_backtest.py --start 2020-01-01 --end 2026-04-25
    python3 femisagent_backtest.py --horizon 30 --no-wilson
    python3 femisagent_backtest.py --tickers NVDA AMD TSLA --dry-run
"""
import argparse
import json
import math
import os
import sys
import urllib.request as _urllib_req
from datetime import date, datetime, timedelta, timezone

from femisagent import compute_flags

DEFAULT_WATCHLIST = [
    "TSLA","RKLB","ASTS","MSFT","NVDA","AMD","MU","AMZN","GOOGL","META",
    "ARM","SNPS","KLAC","AMAT","LRCX","TSM","AVGO","MRVL","CRDO","ALAB",
    "FN","AMKR","COHR","AAOI","HOOD","POWL","NBIS","TMUS","ADI","TXN",
    "QCOM","ASML","LITE","SMTC","HIMS","MP","JBL","IBIT","IREN","GEV",
    "BE","CLS","CRWV","VST","NEE","RIOT","CORZ","HUT","GDX"
]


class Bar:
    __slots__ = ("date", "open", "high", "low", "close", "volume")
    def __init__(self, d, o, h, l, c, v):
        self.date, self.open, self.high, self.low, self.close, self.volume = d, o, h, l, c, v


def fetch_yf_bars(tickers, start, end):
    """Returns {ticker: [Bar, ...]} oldest-first. Skips tickers with no data."""
    import yfinance as yf
    import pandas as pd
    out = {}
    print(f"[backtest] yf.download({len(tickers)} tickers, {start} → {end})…", flush=True)
    df = yf.download(
        tickers, start=start, end=end, group_by="ticker",
        auto_adjust=True, progress=False, threads=True,
    )
    if df is None or df.empty:
        return out

    # yfinance may return multi-index columns even for a single ticker when
    # group_by="ticker". Detect and unwrap accordingly.
    is_multi = isinstance(df.columns, pd.MultiIndex)
    for sym in tickers:
        try:
            sub = df[sym] if is_multi else df
        except (KeyError, AttributeError):
            continue
        bars = _df_to_bars(sub)
        if bars:
            out[sym] = bars
    return out


def _df_to_bars(sub):
    """Convert a yfinance OHLCV dataframe to a list of Bar."""
    bars = []
    sub = sub.dropna(subset=["Close"])
    for idx, row in sub.iterrows():
        c = float(row["Close"])
        if math.isnan(c) or c <= 0:
            continue
        bars.append(Bar(
            d=idx.strftime("%Y-%m-%d"),
            o=float(row["Open"]),
            h=float(row["High"]),
            l=float(row["Low"]),
            c=c,
            v=int(row["Volume"]) if not math.isnan(row["Volume"]) else 0,
        ))
    return bars


def run_backtest(bars_by_ticker, horizon_days=60, lookback_bars=90,
                 spy_ret_20d_by_date=None, spy_ret_60d_by_date=None,
                 spy_60d_dd_by_date=None, investigate_flag=None):
    """For each ticker, slide a window, compute flags, measure forward returns.

    v1.4: also passes spy_ret_60d and spy_60d_dd_pct to compute_flags for
    regime-gated flags (PARABOLIC_RECOVERY).
    """
    flag_returns = {}
    all_window_returns = []
    fires_per_ticker = {}
    investigation = []
    for sym, bars in bars_by_ticker.items():
        if len(bars) < lookback_bars + horizon_days + 5:
            continue
        fires_per_ticker[sym] = 0
        for i in range(lookback_bars, len(bars) - horizon_days):
            window = bars[i - lookback_bars: i + 1]
            date_key = bars[i].date
            spy_20d = spy_ret_20d_by_date.get(date_key) if spy_ret_20d_by_date else None
            spy_60d = spy_ret_60d_by_date.get(date_key) if spy_ret_60d_by_date else None
            spy_dd  = spy_60d_dd_by_date.get(date_key)  if spy_60d_dd_by_date  else None
            flags = compute_flags(window, spy_ret_20d=spy_20d,
                                  spy_ret_60d=spy_60d, spy_60d_dd_pct=spy_dd)
            entry = bars[i].close
            future = bars[i + horizon_days].close
            ret = (future / entry - 1) * 100.0
            all_window_returns.append(ret)
            for flag in flags:
                if flag in ("NEUTRAL", "INSUFFICIENT_DATA", "NO_DATA"):
                    continue
                flag_returns.setdefault(flag, []).append(ret)
                fires_per_ticker[sym] += 1
            if investigate_flag and investigate_flag in flags:
                # Max drawdown during the forward window (i .. i+horizon)
                fwd_closes = [b.close for b in bars[i: i + horizon_days + 1]]
                min_close = min(fwd_closes)
                max_dd = (min_close / entry - 1) * 100.0
                investigation.append({
                    "sym": sym,
                    "date": bars[i].date,
                    "year": bars[i].date[:4],
                    "fwd_ret": ret,
                    "max_dd_pct": max_dd,
                })

    n_baseline = len(all_window_returns)
    if n_baseline > 0:
        wins_baseline = sum(1 for r in all_window_returns if r > 0)
        baseline = {
            "n": n_baseline,
            "win_rate_pct": round(wins_baseline / n_baseline * 100.0, 1),
            "avg_ret_pct": round(sum(all_window_returns) / n_baseline, 2),
        }
    else:
        baseline = {"n": 0, "win_rate_pct": 0.0, "avg_ret_pct": 0.0}

    stats = {}
    for flag, rets in flag_returns.items():
        n = len(rets)
        if n == 0:
            continue
        wins = sum(1 for r in rets if r > 0)
        wr_pct = round(wins / n * 100.0, 1)
        avg_ret = round(sum(rets) / n, 2)
        stats[flag] = {
            "n": n,
            "win_rate_pct": wr_pct,
            "avg_ret_pct": avg_ret,
            "excess_ret_pct": round(avg_ret - baseline["avg_ret_pct"], 2),
            "excess_wr_pct": round(wr_pct - baseline["win_rate_pct"], 1),
        }
    return stats, fires_per_ticker, baseline, investigation


def wilson_lower(p, n, z=1.96):
    if n == 0:
        return 0.0
    num = p + z * z / (2 * n) - z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    den = 1 + z * z / n
    return num / den


def apply_wilson_demote(stats, min_n=10, min_wilson_lower=0.5, z=1.96):
    """Neutralize flags failing BOTH (a) low statistical confidence AND
    (b) negative excess return. Pure-WR rule killed fat-tail-positive
    signals like SQUEEZE_BULLISH (54% WR + +30% avg_ret); v1.4 requires
    the flag to ALSO be unprofitable in excess terms before demoting.
    """
    demoted = []
    for flag, s in stats.items():
        p = s["win_rate_pct"] / 100.0
        n = s["n"]
        excess = s.get("excess_ret_pct", s["avg_ret_pct"])

        # n<min_n always demotes (sample size unreliable) but ONLY if
        # the unreliable signal is also unprofitable.
        if n < min_n:
            if excess >= 0:
                continue
            reason = f"n<{min_n} AND excess={excess:+.2f}%<0"
        else:
            wl = wilson_lower(p, n, z)
            if wl >= min_wilson_lower:
                continue
            if excess >= 0:
                continue  # Mediocre WR but positive excess → keep (fat-tail signal)
            reason = f"Wilson_lower={wl:.3f}<{min_wilson_lower} AND excess={excess:+.2f}%<0"

        demoted.append({
            "flag": flag,
            "n": n,
            "prev_wr_pct": s["win_rate_pct"],
            "prev_avg_ret_pct": s["avg_ret_pct"],
            "prev_excess_ret_pct": excess,
            "reason": reason,
        })
        s["win_rate_pct"] = 50.0
        s["avg_ret_pct"] = 0.0
        if "excess_ret_pct" in s:
            s["excess_ret_pct"] = 0.0
    return demoted


def build_payload(version_tag, period_label, universe, stats, demoted, args, baseline=None):
    """Build the femisapien_backtest_runs row body."""
    items = sorted(
        stats.items(),
        key=lambda kv: (kv[1]["win_rate_pct"] / 100.0) * kv[1]["avg_ret_pct"],
        reverse=True,
    )
    flag_arr = [
        {
            "flag": flag,
            "n": s["n"],
            "win_rate_pct": s["win_rate_pct"],
            "avg_ret_pct": s["avg_ret_pct"],
            "excess_ret_pct": s.get("excess_ret_pct"),
            "excess_wr_pct": s.get("excess_wr_pct"),
            "priority": i,
        }
        for i, (flag, s) in enumerate(items, 1)
    ]

    pos = [s for s in stats.values() if s["avg_ret_pct"] > 0 and s["n"] > 0]
    if pos:
        total_n = sum(s["n"] for s in pos)
        weighted_wr = sum(s["n"] * s["win_rate_pct"] / 100.0 for s in pos) / total_n
        weighted_ar = sum(s["n"] * s["avg_ret_pct"] for s in pos) / total_n
    else:
        weighted_wr = 0.0
        weighted_ar = 0.0

    total_exec = sum(
        s["n"] for s in stats.values()
        if (s["win_rate_pct"] / 100.0) * s["avg_ret_pct"] >= 80
    )
    total_watch = sum(s["n"] for s in stats.values())

    return {
        "version": version_tag,
        "period": period_label,
        "total_execute_signals": int(total_exec),
        "total_watch_signals": int(total_watch),
        "weighted_win_rate": round(weighted_wr, 4),
        "weighted_avg_return_pct": round(weighted_ar, 2),
        "year_weights": {},
        "universe_tickers": len(universe),
        "year_summary": {},
        "flag_stats": flag_arr,
        "signal_performance": {},
        "regime_stats": {},
        "v38_calibrations": {},
        "raw_metadata": {
            "source": "femisagent_backtest.py",
            "data_provider": "yfinance",
            "horizon_trading_days": args.horizon,
            "lookback_bars_for_flags": args.lookback,
            "wilson_demote_applied": not args.no_wilson,
            "wilson_min_n": args.min_n,
            "wilson_min_lower": args.min_wilson_lower,
            "demoted_flags": demoted,
            "universe_full_list": list(universe),
            "baseline_unconditional": baseline or {},
            "ran_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        },
        "notes": (
            f"Auto-backtest. Period {period_label}. Universe {len(universe)} tickers. "
            f"Horizon {args.horizon}td. Wilson demote: "
            f"{'on (n>=' + str(args.min_n) + ', wl>=' + str(args.min_wilson_lower) + ')' if not args.no_wilson else 'off'}. "
            f"Demoted: {len(demoted)} flags. "
            f"Caveat: yfinance auto-adjusted closes used for forward returns."
        ),
    }


def post_to_supabase(payload):
    """POST a femisapien_backtest_runs row and return new id."""
    url = os.environ.get("SUPABASE_URL", "https://azyxlnbdgehqeifbggef.supabase.co")
    key = os.environ.get("SUPABASE_KEY", "")
    if not key:
        print("[backtest] SUPABASE_KEY not set — skipping write (use --dry-run to silence)")
        return None
    body = json.dumps(payload).encode()
    req = _urllib_req.Request(
        f"{url}/rest/v1/femisapien_backtest_runs",
        data=body,
        headers={
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Prefer": "return=representation",
        },
        method="POST",
    )
    with _urllib_req.urlopen(req, timeout=60) as r:
        result = json.loads(r.read())
    return result[0]["id"] if result else None


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--start", default=(date.today() - timedelta(days=5 * 365)).isoformat())
    p.add_argument("--end", default=date.today().isoformat())
    p.add_argument("--horizon", type=int, default=60, help="Forward-return horizon (trading days)")
    p.add_argument("--lookback", type=int, default=90, help="Bars window passed to compute_flags")
    p.add_argument("--tickers", nargs="+", default=DEFAULT_WATCHLIST)
    p.add_argument("--no-wilson", action="store_true", help="Skip Wilson-CI demotion")
    p.add_argument("--min-n", type=int, default=10)
    p.add_argument("--min-wilson-lower", type=float, default=0.5)
    p.add_argument("--version-tag", default=None)
    p.add_argument("--dry-run", action="store_true", help="Don't POST to Supabase")
    p.add_argument("--investigate-flag", default=None,
                   help="Emit deep-dive analysis (per-year, top tickers, return distribution + max drawdown) for one flag")
    args = p.parse_args()

    if not args.version_tag:
        wilson_desc = (
            f"n>={args.min_n}, wl>={args.min_wilson_lower}"
            if not args.no_wilson else "off"
        )
        args.version_tag = (
            f"FEMISAPIEN Auto-Backtest — yfinance "
            f"{args.start}→{args.end} h{args.horizon}td "
            f"(Wilson: {wilson_desc})"
        )

    period_label = f"{args.start} → {args.end}"
    bars = fetch_yf_bars(args.tickers, args.start, args.end)
    ok_tickers = [t for t, b in bars.items() if b]
    print(f"[backtest] Got bars for {len(ok_tickers)}/{len(args.tickers)} tickers")

    # v1.4: pre-fetch SPY for regime context (20d ret, 60d ret, 60d drawdown).
    spy_bars_dict = fetch_yf_bars(["SPY"], args.start, args.end)
    spy_bars = spy_bars_dict.get("SPY", [])
    spy_ret_20d_by_date = {}
    spy_ret_60d_by_date = {}
    spy_60d_dd_by_date = {}
    for i in range(20, len(spy_bars)):
        spy_ret_20d_by_date[spy_bars[i].date] = (spy_bars[i].close / spy_bars[i-20].close) - 1
    for i in range(60, len(spy_bars)):
        spy_ret_60d_by_date[spy_bars[i].date] = (spy_bars[i].close / spy_bars[i-60].close) - 1
        high_60d = max(b.close for b in spy_bars[i-60:i+1])
        spy_60d_dd_by_date[spy_bars[i].date] = (spy_bars[i].close - high_60d) / high_60d
    print(f"[backtest] SPY benchmark: {len(spy_ret_20d_by_date)} dates w/ ret_20d, "
          f"{len(spy_ret_60d_by_date)} w/ ret_60d + 60d-drawdown")

    stats, fires_per_ticker, baseline, investigation = run_backtest(
        bars, horizon_days=args.horizon, lookback_bars=args.lookback,
        spy_ret_20d_by_date=spy_ret_20d_by_date,
        spy_ret_60d_by_date=spy_ret_60d_by_date,
        spy_60d_dd_by_date=spy_60d_dd_by_date,
        investigate_flag=args.investigate_flag,
    )
    print(
        f"[backtest] Unconditional baseline: "
        f"n={baseline['n']} "
        f"WR={baseline['win_rate_pct']:.1f}% "
        f"avg_ret={baseline['avg_ret_pct']:+.2f}% "
        f"(coin-flip entry, same universe, same horizon)"
    )
    print(f"[backtest] Per-flag stats (excess = vs baseline):")
    sorted_items = sorted(
        stats.items(),
        key=lambda kv: -(kv[1]["win_rate_pct"] / 100.0) * kv[1]["avg_ret_pct"],
    )
    for flag, s in sorted_items:
        ev = (s["win_rate_pct"] / 100.0) * s["avg_ret_pct"]
        print(
            f"  {flag:<28} n={s['n']:<5} "
            f"WR={s['win_rate_pct']:>5.1f}% "
            f"avg_ret={s['avg_ret_pct']:>+7.2f}% "
            f"EV={ev:>+7.2f}  | "
            f"excess_ret={s['excess_ret_pct']:>+6.2f}% "
            f"excess_wr={s['excess_wr_pct']:>+5.1f}%"
        )

    demoted = []
    if not args.no_wilson:
        demoted = apply_wilson_demote(
            stats, min_n=args.min_n, min_wilson_lower=args.min_wilson_lower
        )
        if demoted:
            print(f"[backtest] Wilson-CI demoted {len(demoted)} flags:")
            for d in demoted:
                print(
                    f"  {d['flag']:<28} reason={d['reason']:<24} "
                    f"prev_WR={d['prev_wr_pct']:>5.1f}% prev_avg_ret={d['prev_avg_ret_pct']:>+7.2f}%"
                )

    if args.investigate_flag:
        print(f"\n[backtest] === Investigation: {args.investigate_flag} ===")
        if not investigation:
            print(f"  No fires of {args.investigate_flag} in this sample.")
        else:
            n_all = len(investigation)
            rets_sorted = sorted(r["fwd_ret"] for r in investigation)
            dds_sorted  = sorted(r["max_dd_pct"] for r in investigation)
            print(f"Overall fwd_ret distribution (n={n_all}):")
            for label, idx in [("min", 0), ("p10", n_all // 10), ("p25", n_all // 4),
                               ("med", n_all // 2), ("p75", 3 * n_all // 4),
                               ("p90", 9 * n_all // 10), ("max", n_all - 1)]:
                print(f"  {label:>4}: fwd_ret={rets_sorted[idx]:+7.2f}%  max_dd={dds_sorted[idx]:+7.2f}%")
            neg = sum(1 for r in rets_sorted if r < 0)
            print(f"  Negative fwd_ret fraction: {neg / n_all * 100:.1f}% ({neg}/{n_all})")
            avg_dd = sum(r["max_dd_pct"] for r in investigation) / n_all
            worst_dd = min(r["max_dd_pct"] for r in investigation)
            print(f"  avg max_dd_during_window: {avg_dd:+.2f}%   worst: {worst_dd:+.2f}%")

            print(f"\nPer-year breakdown:")
            by_year = {}
            for r in investigation:
                by_year.setdefault(r["year"], []).append(r)
            for year in sorted(by_year):
                rs = by_year[year]
                n = len(rs)
                rets = sorted(r["fwd_ret"] for r in rs)
                avg = sum(rets) / n
                med = rets[n // 2]
                p25 = rets[max(0, n // 4)]
                p75 = rets[min(n - 1, 3 * n // 4)]
                avg_dd = sum(r["max_dd_pct"] for r in rs) / n
                worst_dd = min(r["max_dd_pct"] for r in rs)
                print(f"  {year}: n={n:<4} avg={avg:+6.2f}% med={med:+6.2f}% "
                      f"p25={p25:+6.2f}% p75={p75:+6.2f}% "
                      f"avg_dd={avg_dd:+6.2f}% worst_dd={worst_dd:+6.2f}%")

            print(f"\nTop 10 tickers by fire count:")
            by_ticker = {}
            for r in investigation:
                by_ticker.setdefault(r["sym"], []).append(r)
            for sym, rs in sorted(by_ticker.items(), key=lambda x: -len(x[1]))[:10]:
                rets = [r["fwd_ret"] for r in rs]
                dds  = [r["max_dd_pct"] for r in rs]
                print(f"  {sym:<6} n={len(rs):<4} avg_fwd={sum(rets)/len(rets):+6.2f}% "
                      f"avg_dd={sum(dds)/len(dds):+6.2f}%")
        print()

    payload = build_payload(args.version_tag, period_label, args.tickers, stats, demoted, args, baseline=baseline)

    if args.dry_run:
        print("[backtest] --dry-run: not POSTing to Supabase. Payload preview:")
        preview = {k: v for k, v in payload.items()
                   if k not in ("raw_metadata", "flag_stats")}
        preview["flag_stats_count"] = len(payload["flag_stats"])
        preview["raw_metadata_keys"] = list(payload["raw_metadata"].keys())
        print(json.dumps(preview, indent=2, default=str))
        return

    new_id = post_to_supabase(payload)
    if new_id is not None:
        print(
            f"[backtest] Inserted femisapien_backtest_runs id={new_id}. "
            f"Live agent picks up on next scan."
        )


if __name__ == "__main__":
    main()
