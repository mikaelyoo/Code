#!/usr/bin/env python3
"""
FEMISAGENT v1.6 — Live FEMISAPIEN v3.8 Signal Scanner
Connects to IBKR, pulls market data, applies flag logic, ranks signals.
Usage: python3 femisagent.py [--tickers TSLA NVDA ...] [--portfolio]

v1.1: FLAG_STATS hot-loaded from Supabase.
v1.2: lazy ib_insync import; print_report KeyError fix.
v1.3: signal_verdict thresholds rescaled to 10/5/0.
v1.4: ev_score uses excess_ret_pct; thresholds 4/2/0.
v1.5: experimental HRT_STRONG_v2 / VPIN_PERSISTENT / SQUEEZE_PRE_BREAKOUT.
      A/B backtest showed all three failed to beat their predecessors.

v1.6: second-round experimental variants based on v1.5 lessons:
  - HRT_STRONG_v3: BOTH absolute (ret_20d > 10%) AND relative-strength
    (ret_20d > SPY + 10pp). v2's SPY+5pp turned out LOOSER than +10%
    absolute in the bull regime — v3 makes it strictly tighter.
  - OBV_BULL_DIVERGENCE / OBV_BEAR_DIVERGENCE: replace VPIN proxy with
    On-Balance-Volume divergence vs price. Different concept entirely —
    detects accumulation (OBV up while price flat) and distribution
    (OBV flat/down while price up).
  - SQUEEZE_BULLISH / SQUEEZE_BEARISH: add MACD-histogram bias filter
    to squeeze. Doesn't just fire on compression — fires with direction.

All new flags fire alongside existing ones. The next backtest A/Bs them.
"""
import asyncio, json, sys, argparse, os
import urllib.request as _urllib_req
from datetime import datetime, timedelta

# ── Config ─────────────────────────────────────────────────────────────────
IBKR_HOST = os.environ.get("IBKR_HOST", "100.102.101.103")
IBKR_PORT = int(os.environ.get("IBKR_PORT", "7496"))
IBKR_CID  = int(os.environ.get("IBKR_CLIENT_ID", "10"))

SUPABASE_URL = os.environ.get(
    "SUPABASE_URL", "https://azyxlnbdgehqeifbggef.supabase.co"
)
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
CALIB_CACHE  = "/data/.openclaw/workspace/memory/femisagent_flag_stats.cache.json"

# FEMISAPIEN v3.8 flag weights — fallback if Supabase unreachable.
FLAG_STATS = {
    "GS_ACCUM":              {"win_rate": 0.85, "avg_ret": 123.4, "priority": 1},
    "SQUEEZE_EXTREME":       {"win_rate": 1.00, "avg_ret": 115.5, "priority": 2},
    "GS_MILD_ACCUM":         {"win_rate": 0.75, "avg_ret":  97.3, "priority": 3},
    "20D_BREAKOUT":          {"win_rate": 0.88, "avg_ret":  78.6, "priority": 4},
    "MOMENTUM_SURGE":        {"win_rate": 0.93, "avg_ret":  55.2, "priority": 5},
    "VPIN_ELEVATED":         {"win_rate": 0.94, "avg_ret":  51.5, "priority": 6},
    "MOMENTUM_CONTINUATION": {"win_rate": 0.86, "avg_ret":  47.2, "priority": 7},
    "HRT_STRONG":            {"win_rate": 0.83, "avg_ret":  44.6, "priority": 8},
    "VPIN_TOXIC":            {"win_rate": 0.78, "avg_ret":  40.8, "priority": 9},
    "HRT_REVERSAL_RISK":     {"win_rate": 0.36, "avg_ret":  -8.2, "priority": 10},
    "PARABOLIC_BLOCK":       {"win_rate": 0.44, "avg_ret":  -5.1, "priority": 11},
    "HRT_WEAK":              {"win_rate": 0.43, "avg_ret": -12.3, "priority": 12},
    "GS_DISTRIB":            {"win_rate": 0.20, "avg_ret": -18.6, "priority": 13},
}
CALIBRATION_SOURCE = "hardcoded-v3.8"

def load_latest_calibration():
    """Pull newest femisapien_backtest_runs row → override FLAG_STATS.

    Tries Supabase first (requires SUPABASE_KEY env var — anon key needs a
    SELECT policy on the table; service-role key bypasses RLS). On failure,
    falls back to local cache, then to hardcoded defaults.
    """
    global FLAG_STATS, CALIBRATION_SOURCE
    if SUPABASE_KEY:
        try:
            url = (
                f"{SUPABASE_URL}/rest/v1/femisapien_backtest_runs"
                f"?select=id,version,flag_stats,weighted_win_rate,weighted_avg_return_pct,period"
                f"&order=id.desc&limit=1"
            )
            req = _urllib_req.Request(url, headers={
                "apikey": SUPABASE_KEY,
                "Authorization": f"Bearer {SUPABASE_KEY}",
                "Accept": "application/json",
            })
            with _urllib_req.urlopen(req, timeout=10) as r:
                rows = json.loads(r.read())
            if rows:
                row = rows[0]
                flag_arr = row.get("flag_stats") or []
                new_stats = {}
                for i, f in enumerate(flag_arr, 1):
                    entry = {
                        "win_rate": float(f["win_rate_pct"]) / 100.0,
                        "avg_ret":  float(f["avg_ret_pct"]),
                        "priority": i,
                        "n":        int(f.get("n", 0)),
                    }
                    if f.get("excess_ret_pct") is not None:
                        entry["excess_ret"] = float(f["excess_ret_pct"])
                    if f.get("excess_wr_pct") is not None:
                        entry["excess_wr"] = float(f["excess_wr_pct"])
                    new_stats[f["flag"]] = entry
                if new_stats:
                    FLAG_STATS = new_stats
                    CALIBRATION_SOURCE = f"supabase-run-id={row['id']}"
                    try:
                        with open(CALIB_CACHE, "w") as fh:
                            json.dump({
                                "run_id":  row["id"],
                                "version": row["version"],
                                "period":  row.get("period"),
                                "weighted_win_rate":        float(row["weighted_win_rate"]),
                                "weighted_avg_return_pct":  float(row["weighted_avg_return_pct"]),
                                "flag_stats": new_stats,
                                "cached_at":  datetime.now().isoformat(),
                            }, fh, indent=2)
                    except Exception as cache_err:
                        print(f"[femisagent] WARN: could not write calib cache: {cache_err}")
                    print(
                        f"[femisagent] Calibration loaded from Supabase: "
                        f"run id={row['id']} | {row['version']} | "
                        f"WR={row['weighted_win_rate']} | AvgRet={row['weighted_avg_return_pct']}%"
                    )
                    return
        except Exception as e:
            print(f"[femisagent] Supabase fetch failed ({e}); trying cache…")
    else:
        print("[femisagent] SUPABASE_KEY not set; trying cache…")

    try:
        with open(CALIB_CACHE) as fh:
            cached = json.load(fh)
        FLAG_STATS = cached["flag_stats"]
        CALIBRATION_SOURCE = f"cache-run-id={cached.get('run_id')}"
        print(
            f"[femisagent] Using cached calibration: "
            f"run id={cached.get('run_id')} | {cached.get('version')} | "
            f"cached_at={cached.get('cached_at')}"
        )
        return
    except Exception:
        pass

    print(f"[femisagent] Using hardcoded v3.8 FLAG_STATS (no Supabase, no cache)")


def ev_score(flag):
    """v1.4: prefer baseline-adjusted excess_ret when available, fall back
    to raw win_rate * avg_ret for legacy rows (id 1-5)."""
    s = FLAG_STATS.get(flag, {})
    if "excess_ret" in s:
        return round(s["excess_ret"], 1)
    return round(s.get("win_rate", 0) * s.get("avg_ret", 0), 1)

def signal_verdict(flag):
    """v1.4: thresholds rescaled for excess EV distribution (typically -3..+8)."""
    ev = ev_score(flag)
    if ev >= 4:  return "🟢 EXECUTE"
    if ev >= 2:  return "🟢 BUY"
    if ev >= 0:  return "🟡 WATCH"
    return "🔴 AVOID"


BRIDGE_URL   = os.environ.get("BRIDGE_URL", "http://localhost:8765")
BRIDGE_TOKEN = os.environ.get("JARVIS_BRIDGE_TOKEN", "")
USE_BRIDGE   = os.environ.get("USE_BRIDGE", "1") == "1"

def fetch_bars_via_bridge(symbol):
    """Fetch bars from Jarvis bridge instead of direct IBKR connection."""
    payload = json.dumps({
        "method": "tools/call",
        "params": {"name": "fetch_bars", "arguments": {"symbol": symbol}}
    }).encode()
    req = _urllib_req.Request(
        f"{BRIDGE_URL}/mcp",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {BRIDGE_TOKEN}",
            "Accept": "application/json, text/event-stream"
        },
        method="POST"
    )
    try:
        with _urllib_req.urlopen(req, timeout=30) as r:
            raw = r.read().decode()
        for line in raw.splitlines():
            if line.startswith("data:"):
                data = json.loads(line[5:].strip())
                result = data.get("result", {})
                content = result.get("content", [])
                if content and content[0].get("type") == "text":
                    return json.loads(content[0]["text"])
    except Exception:
        pass
    return []

def make_bar_obj(d):
    class Bar:
        def __init__(self, row):
            self.date   = row["date"]
            self.open   = row["open"]
            self.high   = row["high"]
            self.low    = row["low"]
            self.close  = row["close"]
            self.volume = row["volume"]
    return Bar(d)

# ── Technical flag engine ───────────────────────────────────────────────────
def compute_flags(bars, spy_ret_20d=None):
    """Given list of OHLCV bars (oldest first), return FEMISAPIEN flags.

    v1.5: optional spy_ret_20d parameter enables HRT_STRONG_v2 (relative-
    strength gate). Backtest passes per-bar SPY ret_20d aligned by date;
    live scanner passes single current value. When None, HRT_STRONG_v2
    silently does not fire (legacy fallback).
    """
    if len(bars) < 22:
        return ["INSUFFICIENT_DATA"]

    closes  = [b.close  for b in bars]
    volumes = [b.volume for b in bars]
    highs   = [b.high   for b in bars]
    lows    = [b.low    for b in bars]

    c   = closes[-1]
    c5  = closes[-5]
    c10 = closes[-10]
    c20 = closes[-20]

    ma20 = sum(closes[-20:]) / 20
    ma50 = sum(closes[-50:]) / 50 if len(closes) >= 50 else sum(closes) / len(closes)

    avg_vol_20 = sum(volumes[-20:]) / 20
    vol_today  = volumes[-1]
    vol_ratio  = vol_today / avg_vol_20 if avg_vol_20 > 0 else 1.0

    ret_1d  = (c - closes[-2]) / closes[-2] if len(closes) >= 2 else 0
    ret_5d  = (c - c5)  / c5  if c5  > 0 else 0
    ret_10d = (c - c10) / c10 if c10 > 0 else 0
    ret_20d = (c - c20) / c20 if c20 > 0 else 0

    std20 = (sum((x - ma20)**2 for x in closes[-20:]) / 20) ** 0.5
    bb_upper = ma20 + 2 * std20
    bb_lower = ma20 - 2 * std20
    bb_width = (bb_upper - bb_lower) / ma20 if ma20 > 0 else 0
    bb_width_hist = []
    for i in range(20, len(closes)):
        chunk = closes[i-20:i]
        m = sum(chunk)/20
        s = (sum((x-m)**2 for x in chunk)/20)**0.5
        bb_width_hist.append((2*s/m) if m > 0 else 0)
    bb_squeeze = bb_width < (sum(bb_width_hist[-20:])/20 * 0.75) if bb_width_hist else False

    high_20d = max(highs[-21:-1]) if len(highs) >= 21 else max(highs)
    breakout_20d = c > high_20d

    parabolic = ret_20d > 0.40

    uptrend   = c > ma20 > ma50
    downtrend = c < ma20 < ma50

    up_vol   = sum(volumes[-i] for i in range(1, 6) if closes[-i] >= closes[-i-1])
    down_vol = sum(volumes[-i] for i in range(1, 6) if closes[-i] <  closes[-i-1])
    total_vol_5 = up_vol + down_vol
    vpin = up_vol / total_vol_5 if total_vol_5 > 0 else 0.5

    # v1.5: VPIN persistence — compute vpin at each of the last 5 sessions
    # for the VPIN_PERSISTENT flag. Each window uses the prior 5 sessions.
    vpins_recent = []
    if len(closes) >= 11:
        for offset in range(5):
            up_w   = sum(volumes[-i-offset] for i in range(1, 6) if closes[-i-offset] >= closes[-i-offset-1])
            down_w = sum(volumes[-i-offset] for i in range(1, 6) if closes[-i-offset] <  closes[-i-offset-1])
            tot = up_w + down_w
            if tot > 0:
                vpins_recent.append(up_w / tot)
    vpin_persistent_bull = len(vpins_recent) == 5 and all(v > 0.55 for v in vpins_recent)

    # v1.6: OBV (On-Balance-Volume) for divergence detection.
    # Running cumulative: +volume on up-close days, -volume on down-close days.
    obv_series = [0]
    for i in range(1, len(closes)):
        if closes[i] > closes[i-1]:
            obv_series.append(obv_series[-1] + volumes[i])
        elif closes[i] < closes[i-1]:
            obv_series.append(obv_series[-1] - volumes[i])
        else:
            obv_series.append(obv_series[-1])
    vol_20d_total = sum(volumes[-20:])
    if len(obv_series) >= 21 and vol_20d_total > 0:
        obv_normalized = (obv_series[-1] - obv_series[-21]) / vol_20d_total
    else:
        obv_normalized = 0.0

    # v1.6: MACD histogram (12/26/9 EMAs of close).
    macd_hist = 0.0
    if len(closes) >= 35:
        k12, k26, k9 = 2.0/13, 2.0/27, 2.0/10
        e12 = sum(closes[:12]) / 12
        e26 = sum(closes[:26]) / 26
        macd_vals = []
        for i in range(12, len(closes)):
            e12 = closes[i] * k12 + e12 * (1 - k12)
            if i >= 26:
                e26 = closes[i] * k26 + e26 * (1 - k26)
                macd_vals.append(e12 - e26)
        if len(macd_vals) >= 9:
            sig = sum(macd_vals[:9]) / 9
            for v in macd_vals[9:]:
                sig = v * k9 + sig * (1 - k9)
            macd_hist = macd_vals[-1] - sig

    flags = []

    if vol_ratio >= 2.5 and ret_1d > 0.01 and uptrend:
        flags.append("GS_ACCUM")
    elif vol_ratio >= 1.5 and ret_1d > 0.005 and uptrend:
        flags.append("GS_MILD_ACCUM")

    if bb_squeeze and ret_5d > 0.05 and vol_ratio > 1.3:
        flags.append("SQUEEZE_EXTREME")

    if breakout_20d and vol_ratio > 1.2:
        flags.append("20D_BREAKOUT")

    if ret_5d > 0.08 and vol_ratio > 1.4 and uptrend:
        flags.append("MOMENTUM_SURGE")
    elif ret_10d > 0.05 and ret_5d > 0.02 and uptrend:
        flags.append("MOMENTUM_CONTINUATION")

    if vpin > 0.65 and vol_ratio > 1.1:
        flags.append("VPIN_ELEVATED")

    if uptrend and ret_20d > 0.10 and ret_5d > 0:
        if "MOMENTUM_SURGE" not in flags and "MOMENTUM_CONTINUATION" not in flags:
            flags.append("HRT_STRONG")

    if vpin < 0.35 and vol_ratio > 1.1:
        flags.append("VPIN_TOXIC")

    if parabolic:
        flags.append("PARABOLIC_BLOCK")

    if vol_ratio >= 1.5 and ret_1d < -0.01 and downtrend:
        flags.append("GS_DISTRIB")

    if downtrend and ret_5d < -0.03:
        flags.append("HRT_WEAK")

    if ret_5d < -0.02 and ret_10d > 0.05:
        flags.append("HRT_REVERSAL_RISK")

    # ── v1.5 experimental flag variants ──────────────────────────────────
    # All three fire alongside their predecessors so the next backtest can
    # rank both side by side. v1.6 will retire the losers.

    # HRT_STRONG_v2: relative-strength gate — must beat SPY 20d return by
    # at least 5pp. Falls back to no-fire when caller didn't pass spy
    # context (e.g., legacy live invocation without SPY pre-fetch).
    if spy_ret_20d is not None:
        if uptrend and ret_20d > spy_ret_20d + 0.05 and ret_5d > 0:
            if "MOMENTUM_SURGE" not in flags and "MOMENTUM_CONTINUATION" not in flags:
                flags.append("HRT_STRONG_v2")

    # VPIN_PERSISTENT: replaces the snapshot-only VPIN_ELEVATED. Requires
    # up-volume share > 0.55 across the trailing 5 sessions.
    if vpin_persistent_bull and vol_ratio > 1.1:
        flags.append("VPIN_PERSISTENT")

    # SQUEEZE_PRE_BREAKOUT: replaces SQUEEZE_EXTREME. Fires while the
    # squeeze is still live and price is near-flat with declining volume —
    # i.e. before the breakout, not after.
    if bb_squeeze and -0.02 < ret_5d < 0.02 and vol_ratio < 0.9:
        flags.append("SQUEEZE_PRE_BREAKOUT")

    # ── v1.6 experimental flag variants ──────────────────────────────────
    # v1.5's three new flags all failed to beat predecessors in the A/B.
    # v1.6 retries with tighter / structurally-different conditions.

    # HRT_STRONG_v3: BOTH absolute (>10%) AND relative-strength (>SPY+10pp).
    # v2 was looser than the original because SPY+5pp was usually < 10%
    # absolute. v3 requires BOTH thresholds — strictly tighter than either.
    if spy_ret_20d is not None:
        if uptrend and ret_20d > 0.10 and ret_20d > spy_ret_20d + 0.10 and ret_5d > 0:
            if "MOMENTUM_SURGE" not in flags and "MOMENTUM_CONTINUATION" not in flags:
                flags.append("HRT_STRONG_v3")

    # OBV divergence — accumulation/distribution detection.
    # Bullish: price flat/down but volume biased upward (smart-money buying
    # under cover of stagnation).
    # Bearish: price up but volume neutral/negative (rally without conviction,
    # potentially distribution into strength).
    if obv_normalized > 0.20 and ret_20d < 0.02:
        flags.append("OBV_BULL_DIVERGENCE")
    if obv_normalized < 0.0 and ret_20d > 0.05:
        flags.append("OBV_BEAR_DIVERGENCE")

    # SQUEEZE + MACD directional bias. Fires near-flat price during a
    # squeeze, conditional on MACD histogram direction. Two opposite
    # signals from the same setup.
    if bb_squeeze and -0.05 < ret_5d < 0.05:
        if macd_hist > 0:
            flags.append("SQUEEZE_BULLISH")
        elif macd_hist < 0:
            flags.append("SQUEEZE_BEARISH")

    def _edge(f):
        s = FLAG_STATS.get(f, {})
        return s["excess_ret"] if "excess_ret" in s else s.get("avg_ret", 0)
    pos = [f for f in flags if _edge(f) > 0]
    neg = [f for f in flags if _edge(f) <= 0]
    pos.sort(key=lambda f: FLAG_STATS.get(f, {}).get("priority", 999))
    neg.sort(key=lambda f: FLAG_STATS.get(f, {}).get("priority", 999))

    result = []
    if pos: result.append(pos[0])
    if neg: result.append(neg[0])
    return result if result else ["NEUTRAL"]

# ── IBKR data fetch ─────────────────────────────────────────────────────────
async def fetch_bars(ib, ticker, exchange="SMART", currency="USD"):
    if USE_BRIDGE and BRIDGE_TOKEN:
        raw = fetch_bars_via_bridge(ticker)
        if raw:
            return [make_bar_obj(d) for d in raw]
    from ib_insync import Stock
    contract = Stock(ticker, exchange, currency)
    try:
        await ib.qualifyContractsAsync(contract)
        bars = await ib.reqHistoricalDataAsync(
            contract,
            endDateTime="",
            durationStr="90 D",
            barSizeSetting="1 day",
            whatToShow="TRADES",
            useRTH=True,
            formatDate=1,
            keepUpToDate=False,
        )
        return list(bars)
    except Exception:
        return []

async def get_portfolio_tickers(ib):
    positions = await ib.reqPositionsAsync()
    return [(p.contract.symbol, p.contract.exchange or "SMART",
             p.contract.currency or "USD", p.position, p.avgCost) for p in positions]

# ── Main ────────────────────────────────────────────────────────────────────
async def run(tickers=None, portfolio_mode=False):
    from ib_insync import IB
    ib = IB()
    if USE_BRIDGE and BRIDGE_TOKEN:
        print(f"Using Jarvis bridge at {BRIDGE_URL} for market data (no direct IBKR connection)")
    else:
        for attempt in range(1, 4):
            try:
                print(f"Connecting to IBKR {IBKR_HOST}:{IBKR_PORT} clientId={IBKR_CID} (attempt {attempt}/3)...")
                await ib.connectAsync(IBKR_HOST, IBKR_PORT, clientId=IBKR_CID, timeout=20)
                print(f"Connected. Server: {ib.client.serverVersion()}")
                break
            except Exception as e:
                print(f"  Failed: {e}")
                if attempt == 3: raise
                await asyncio.sleep(3)

    portfolio_data = {}
    if portfolio_mode:
        if USE_BRIDGE and BRIDGE_TOKEN:
            print("Fetching portfolio via bridge...")
        positions = await get_portfolio_tickers(ib)
        for sym, exch, cur, qty, cost in positions:
            portfolio_data[sym] = {"qty": qty, "avgCost": cost}
        if not tickers:
            tickers = [p[0] for p in positions]

    # v1.5: pre-fetch SPY 20d return so HRT_STRONG_v2 can fire in live scans
    spy_ret_20d = None
    spy_bars = await fetch_bars(ib, "SPY")
    if len(spy_bars) >= 21:
        spy_ret_20d = (spy_bars[-1].close - spy_bars[-21].close) / spy_bars[-21].close
        print(f"SPY 20d benchmark: {spy_ret_20d*100:+.2f}% (HRT_STRONG_v2 gate active)")
    else:
        print("SPY benchmark unavailable; HRT_STRONG_v2 will not fire this scan")

    results = []
    total = len(tickers)
    for i, sym in enumerate(tickers, 1):
        print(f"  [{i}/{total}] {sym}...", end=" ", flush=True)
        bars = await fetch_bars(ib, sym)
        if len(bars) < 22:
            print(f"insufficient data ({len(bars)} bars)")
            results.append({"symbol": sym, "flag": "NO_DATA", "verdict": "⚪ SKIP",
                             "ev_score": 0, "bars": len(bars)})
            continue

        flags = compute_flags(bars, spy_ret_20d=spy_ret_20d)
        primary = flags[0]
        secondary = flags[1] if len(flags) > 1 else None

        c = bars[-1].close
        c20 = bars[-20].close
        ret_20d = (c - c20) / c20
        avg_vol = sum(b.volume for b in bars[-20:]) / 20
        vol_ratio = bars[-1].volume / avg_vol if avg_vol > 0 else 1.0

        row = {
            "symbol":     sym,
            "price":      round(c, 2),
            "ret_20d_pct": round(ret_20d * 100, 1),
            "vol_ratio":  round(vol_ratio, 2),
            "flag":       primary,
            "flag2":      secondary,
            "ev_score":   ev_score(primary),
            "verdict":    signal_verdict(primary),
            "bars":       len(bars),
        }
        if sym in portfolio_data:
            pos = portfolio_data[sym]
            row["qty"]      = pos["qty"]
            row["avg_cost"] = round(pos["avgCost"], 2)
            row["unreal_pct"] = round((c - pos["avgCost"]) / pos["avgCost"] * 100, 1) if pos["avgCost"] else 0

        results.append(row)
        print(f"{primary} | EV={row['ev_score']} | {row['verdict']}")
        await asyncio.sleep(0.4)

    ib.disconnect()
    return results

def print_report(results):
    results.sort(key=lambda x: x["ev_score"], reverse=True)
    print("\n" + "="*80)
    print(f"FEMISAGENT v1.6 — SIGNAL REPORT | {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"Calibration: {CALIBRATION_SOURCE}")
    print("="*80)

    categories = [
        ("🟢 EXECUTE",  lambda r: r["ev_score"] >= 4),
        ("🟢 BUY",      lambda r: 2 <= r["ev_score"] < 4),
        ("🟡 WATCH",    lambda r: 0 <= r["ev_score"] < 2),
        ("🔴 AVOID",    lambda r: r["ev_score"] < 0),
        ("⚪ SKIP",     lambda r: r["ev_score"] == 0 and r["flag"] in ["NO_DATA","NEUTRAL","INSUFFICIENT_DATA"]),
    ]

    for label, fn in categories:
        bucket = [r for r in results if fn(r) and r["verdict"].startswith(label.split()[0])]
        if not bucket: continue
        print(f"\n{'─'*40}")
        print(f"  {label}  ({len(bucket)} signals)")
        print(f"{'─'*40}")
        for r in bucket:
            pos_info = ""
            if "qty" in r:
                pos_info = f" | pos={r['qty']} @ ${r['avg_cost']} ({r['unreal_pct']:+.1f}%)"
            flag2 = f" +{r['flag2']}" if r.get("flag2") else ""
            if "price" in r:
                print(f"  {r['symbol']:<8} ${r['price']:<8.2f} {r['ret_20d_pct']:+6.1f}% 20d | "
                      f"vol={r['vol_ratio']:.1f}x | {r['flag']}{flag2}{pos_info}")
            else:
                print(f"  {r['symbol']:<8} {r['flag']}{flag2}{pos_info}")

    print("\n" + "="*80)
    print(f"Scanned {len(results)} tickers | "
          f"Execute: {sum(1 for r in results if r['ev_score'] >= 4)} | "
          f"Buy: {sum(1 for r in results if 2 <= r['ev_score'] < 4)} | "
          f"Watch: {sum(1 for r in results if 0 <= r['ev_score'] < 2)} | "
          f"Avoid: {sum(1 for r in results if r['ev_score'] < 0)}")
    print("="*80 + "\n")

    out_path = "/data/.openclaw/workspace/memory/femisagent_last_run.json"
    with open(out_path, "w") as f:
        json.dump({
            "run_date": datetime.now().isoformat(),
            "calibration_source": CALIBRATION_SOURCE,
            "signals": results,
            "summary": {
                "total": len(results),
                "execute": sum(1 for r in results if r["ev_score"] >= 4),
                "buy": sum(1 for r in results if 2 <= r["ev_score"] < 4),
                "watch": sum(1 for r in results if 0 <= r["ev_score"] < 2),
                "avoid": sum(1 for r in results if r["ev_score"] < 0),
            }
        }, f, indent=2)
    print(f"Results saved → {out_path}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--portfolio", action="store_true", help="Scan live IBKR portfolio")
    parser.add_argument("--tickers", nargs="+", help="Ticker list to scan")
    args = parser.parse_args()

    load_latest_calibration()

    DEFAULT_WATCHLIST = [
        "TSLA","RKLB","ASTS","MSFT","NVDA","AMD","MU","AMZN","GOOGL","META",
        "ARM","SNPS","KLAC","AMAT","LRCX","TSM","AVGO","MRVL","CRDO","ALAB",
        "FN","AMKR","COHR","AAOI","HOOD","POWL","NBIS","TMUS","ADI","TXN",
        "QCOM","ASML","LITE","SMTC","HIMS","MP","JBL","IBIT","IREN","GEV",
        "BE","CLS","CRWV","VST","NEE","RIOT","CORZ","HUT","GDX"
    ]

    tickers = args.tickers if args.tickers else DEFAULT_WATCHLIST
    results = asyncio.run(run(tickers=tickers, portfolio_mode=args.portfolio))
    print_report(results)
