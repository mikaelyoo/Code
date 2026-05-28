#!/usr/bin/env python3
"""femisa_local — runs femisagent's flag engine on yfinance bars locally.

No bridge, no IBKR, no LLM credits. Pure Python + yfinance.
Use when openclaw or the bridge is unavailable.
"""
import sys, os, importlib.util
from datetime import datetime, timedelta

# Load femisagent.py without triggering its __main__ block
spec = importlib.util.spec_from_file_location(
    "femisagent", os.path.join(os.path.dirname(__file__), "femisagent.py")
)
fa = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fa)

import yfinance as yf


class Bar:
    """Match the bridge's bar shape (date, open, high, low, close, volume)."""
    def __init__(self, date, open_, high, low, close, volume):
        self.date = date
        self.open = open_
        self.high = high
        self.low = low
        self.close = close
        self.volume = volume


def fetch_bars(symbol):
    """Pull ~90 days of daily bars from yfinance."""
    try:
        df = yf.Ticker(symbol).history(period="90d", auto_adjust=False)
        if df is None or df.empty:
            return []
        bars = []
        for ts, row in df.iterrows():
            bars.append(Bar(
                date=ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts,
                open_=float(row["Open"]),
                high=float(row["High"]),
                low=float(row["Low"]),
                close=float(row["Close"]),
                volume=float(row["Volume"]),
            ))
        return bars
    except Exception as e:
        print(f"  fetch failed: {e}", flush=True)
        return []


def fetch_spy_regime():
    """SPY 20d return, 60d return, 60d drawdown — same as femisagent uses."""
    try:
        df = yf.Ticker("SPY").history(period="90d", auto_adjust=False)
        closes = df["Close"].tolist()
        if len(closes) < 60:
            return None, None, None
        c = closes[-1]
        ret_20d = (c - closes[-20]) / closes[-20]
        ret_60d = (c - closes[-60]) / closes[-60]
        high_60d = max(closes[-60:])
        dd_60d = (c - high_60d) / high_60d
        return ret_20d, ret_60d, dd_60d
    except Exception:
        return None, None, None


def scan(tickers):
    print(f"[femisa_local] Scanning {len(tickers)} ticker(s)", flush=True)
    spy_r20, spy_r60, spy_dd = fetch_spy_regime()
    if spy_r20 is not None:
        print(f"SPY regime: 20d={spy_r20*100:+.2f}% 60d={spy_r60*100:+.2f}% 60d-dd={spy_dd*100:+.2f}%", flush=True)

    rows = []
    for i, sym in enumerate(tickers, 1):
        print(f"  [{i}/{len(tickers)}] {sym}...", end=" ", flush=True)
        bars = fetch_bars(sym)
        if len(bars) < 22:
            print(f"no data ({len(bars)} bars)", flush=True)
            rows.append({"symbol": sym, "flag": "NO_DATA", "verdict": "⚪ SKIP",
                         "ev_score": 0, "all_flags": [], "bars": len(bars)})
            continue

        flags = fa.compute_flags(bars, spy_ret_20d=spy_r20,
                                  spy_ret_60d=spy_r60,
                                  spy_60d_dd_pct=spy_dd)
        primary = flags[0]
        secondary = flags[1] if len(flags) > 1 else None
        all_flags = [f for f in flags if f not in ("NEUTRAL", "INSUFFICIENT_DATA", "NO_DATA")]

        c = bars[-1].close
        c20 = bars[-20].close
        ret_20d = (c - c20) / c20
        avg_vol = sum(b.volume for b in bars[-20:]) / 20
        vol_ratio = bars[-1].volume / avg_vol if avg_vol > 0 else 1.0

        row = {
            "symbol": sym, "price": round(c, 2),
            "ret_20d_pct": round(ret_20d * 100, 1),
            "vol_ratio": round(vol_ratio, 2),
            "flag": primary, "flag2": secondary,
            "all_flags": all_flags,
            "ev_score": fa.ev_score(primary),
            "verdict": fa.signal_verdict(primary),
        }

        # Fundamentals: reuse femisagent's helpers (yfinance directly)
        dte = fa.get_earnings_dte(sym)
        row["earnings_dte"] = dte
        if dte is not None and 0 <= dte <= 5:
            row["all_flags"].append("EARNINGS_BLOCK")
            row["ev_score"] = min(row["ev_score"], 0.5)
            row["verdict"] = "🟡 WATCH"
            row["gate"] = f"EARNINGS_IN_{dte}D"

        m = fa.get_beneish_m(sym)
        row["beneish_m"] = m
        if m is not None and m > -1.78:
            row["all_flags"].append("BENEISH_RISK")
            if row["ev_score"] >= 2:
                row["ev_score"] = min(row["ev_score"], 0.5)
                row["verdict"] = "🟡 WATCH"
            row["gate"] = row.get("gate") or f"BENEISH_M={m}"

        if primary == "PARABOLIC_BLOCK" and row["ev_score"] >= 4:
            row["ev_score"] = min(row["ev_score"], 3.5)
            row["verdict"] = "🟢 BUY"
            row["gate"] = row.get("gate") or "PARABOLIC_CAP"

        tf = fa.get_13f_context(sym)
        row["thirteen_f"] = tf

        rows.append(row)
        gate_note = f" [{row['gate']}]" if row.get("gate") else ""
        print(f"{primary} | EV={row['ev_score']} | {row['verdict']}{gate_note}", flush=True)
    return rows


def report(rows):
    rows.sort(key=lambda r: r["ev_score"], reverse=True)
    print("\n" + "=" * 80)
    print(f"FEMISA_LOCAL — SIGNAL REPORT | {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 80)
    bands = [
        ("🟢 EXECUTE",  lambda r: r["ev_score"] >= 4 and r.get("flag") != "NO_DATA"),
        ("🟢 BUY",      lambda r: 2 <= r["ev_score"] < 4),
        ("🟡 WATCH",    lambda r: 0 <= r["ev_score"] < 2 and r.get("flag") != "NO_DATA"),
        ("🔴 AVOID",    lambda r: r["ev_score"] < 0),
        ("⚪ SKIP",     lambda r: r.get("flag") == "NO_DATA"),
    ]
    for label, fn in bands:
        bucket = [r for r in rows if fn(r)]
        if not bucket:
            continue
        print(f"\n{'─'*40}\n  {label} ({len(bucket)} signals)\n{'─'*40}")
        for r in bucket:
            line = f"  {r['symbol']:<8}"
            if "price" in r:
                line += f" ${r['price']:<8.2f} {r['ret_20d_pct']:+6.1f}% 20d | vol={r['vol_ratio']:.1f}x | {r['flag']}"
                if r.get("flag2"):
                    line += f" +{r['flag2']}"
            else:
                line += f" {r['flag']}"
            print(line)
            extras = [f for f in r.get("all_flags", []) if f not in (r.get("flag"), r.get("flag2"))]
            if extras:
                print(f"           +flags: {', '.join(extras)}")
            bits = []
            if r.get("earnings_dte") is not None:
                bits.append(f"earnings={r['earnings_dte']}d")
            if r.get("beneish_m") is not None:
                bits.append(f"M={r['beneish_m']}")
            tf = r.get("thirteen_f") or {}
            if tf.get("inst_pct") is not None:
                bits.append(f"inst={tf['inst_pct']}%")
            if r.get("gate"):
                bits.append(f"gate={r['gate']}")
            if bits:
                print(f"           {' | '.join(bits)}")
            if tf.get("top_3"):
                holders = ", ".join(h["holder"][:18] for h in tf["top_3"])
                print(f"           top3: {holders}")

    print("\n" + "=" * 80)
    print(f"Scanned {len(rows)} | EXECUTE: {sum(1 for r in rows if r['ev_score'] >= 4)} | "
          f"BUY: {sum(1 for r in rows if 2 <= r['ev_score'] < 4)} | "
          f"WATCH: {sum(1 for r in rows if 0 <= r['ev_score'] < 2)} | "
          f"AVOID: {sum(1 for r in rows if r['ev_score'] < 0)}")
    print("=" * 80)


if __name__ == "__main__":
    tickers = sys.argv[1:]
    if not tickers:
        print("Usage: python3 femisa_local.py TICKER1 TICKER2 ...")
        sys.exit(1)
    rows = scan(tickers)
    report(rows)
