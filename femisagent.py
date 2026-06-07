#!/usr/bin/env python3
"""
FEMISAGENT v2.3.2 — Unified multi-engine Signal Engine

v2.3.2 — news_radar emoji-sentiment parser + analyst_intelligence --json:
  - news_radar.py uses --ticker SYM (already correct) but has no --json
    mode; output uses 🟢/🔴/⚪ emoji + media-type markers per headline.
    v2.3.2 counts emoji+marker pairs in the ticker block and computes
    news_score = (bull - bear) / (bull + bear + neutral).
  - analyst_intelligence.py auto-fallback (--ticker → positional) now
    also adds --json on the positional call for clean JSON parsing.
  - 24h news timeframe instead of default 6h for better signal context.

v2.3.1 — fix CLI conventions per backend script:
  - football_field / epv_sotp / hg_dcf use POSITIONAL ticker + --json
  - news_radar / analyst_intelligence: try --ticker first, fall back to positional
  - JSON-mode preferred when supported (cleaner parsing than text scrape)

v2.3 — adds TA Fusion + sentiment as cross-engine backends. Three new
quant overlays alongside femisapien:

  TA Fusion (ta_fusion_consolidated.py)
    Parses the text report (Williams%R, CCI, CMF, OBV trend/div, HL cycle).
    New gates:
      TAF_OVERBOUGHT     — Williams%R + CCI both fire SELL/OVERBOUGHT
                           → demote EXECUTE to BUY (entry too late)
      TAF_OBV_BEAR_DIV   — OBV div: BEARISH → demote EXECUTE to BUY
      TAF_PEAK_CYCLE     — HL cycle PEAK (>30 bars) → demote EXECUTE to BUY
    Confirmation flags (no auto-demote):
      TAF_TROUGH_CYCLE   — HL cycle TROUGH (rare, high-EV entry timing)
      TAF_EXECUTABLE     — TA Fusion's own "Executable: YES" + Conviction>50

  Sentiment dual-backend:
    news_radar.py        — news-headline sentiment aggregation
    analyst_intelligence.py — analyst upgrade/downgrade sentiment
    Each cached 6h. Combined into a single sentiment_score (-1..+1).
    New gates:
      SENT_NEG_HEAVY     — combined sentiment < -0.5 → demote EXECUTE to BUY
      SENT_CONFIRM       — combined sentiment > +0.5 → confirming flag

  Price decomposition (pending — env PRICE_DECOMP_PATH or hunt)
    Skeleton in place; waiting on script identification.

v2.2 — femisapien path resolver. v2.0/v2.1 hard-coded the in-container
path (/data/.openclaw/...) but femisagent often runs on the host where
the same files live at /docker/openclaw-vhii/data/.openclaw/... v2.2
checks the env override first, then both common locations, then degrades
gracefully if neither exists.

v2.1 — yfinance bar fallback. When the bridge's fetch_bars returns empty
(IBKR Gateway down, market-data subscription lapsed, IBKR pacing throttle),
femisagent transparently falls back to yfinance daily bars (15-min delayed
for free tier). Caches + flag logic + all gates unchanged. This kills the
"insufficient data (0 bars)" failure mode that wiped portfolio scans when
IBKR's fetch_bars was silent.

v2.0 — MERGE: femisagent now orchestrates the femisapien_live_signal.py
core engine as a backend (1h cache, subprocess invocation). Single
entry point, both engines' signals visible per ticker. New gates layered
on femisapien quant fields:

  VPIN_TOXIC          — informed-flow toxicity > 0.7 (vpin_signal=TOXIC)
                        demotes EXECUTE → BUY.
  FA_MOMENTUM_EXH     — femisapien momentum_exhaustion_label REDUCE_HALF
                        or TRIM_50 demotes EXECUTE → BUY.
  CROSS_ASSET_RISK    — femisapien cross_asset_gate=False (SPY/sector
                        dislocation) demotes EXECUTE → BUY.
  LPPLS_BUBBLE_HIGH   — femisapien lppls_bubble=BUBBLE + crash_prob > 0.5
                        demotes EXECUTE → BUY.
  HGDCF_PFP           — hgdcf_signal=PRICED_FOR_PERFECTION adds caution
                        flag (no auto-demote, surfaces in report).

  Cross-engine confirmation:
    FA_CONVICTION_N   — when femisapien conviction_pct >= 50, surfaces
                        as a confirming flag (no EV change).

Subprocess path: /data/.openclaw/workspace/scripts/femisapien_live_signal.py
with --ticker SYM --json. 60s timeout per ticker. Graceful degradation:
if the binary isn't present, subprocess times out, or output isn't
parseable, the gate returns None and femisagent's existing rule engine
still produces a verdict.

Cache: 1h TTL keyed on symbol in the same femisagent_fundamentals.cache.json
that holds earnings/Beneish/13F.

v1.10.2 — fix inst_pct parser for new yfinance format. v1.10.1 left every
inst_pct == None because yfinance 0.2.55+ moved the field labels into the
DataFrame index (institutionsPercentHeld) and my parser only inspected
row.values. v1.10.2 reads the index, falls back to the old 2-column shape,
then falls back to Ticker.info["heldPercentInstitutions"]. Cache entries
with inst_pct=None are auto-invalidated early so the upgraded parser takes
effect without waiting for the 7-day TTL.

v1.10.1 hot-fix: yfinance endpoints can hang indefinitely (no built-in
timeout). v1.10 froze on the first 13F call. v1.10.1 wraps every yfinance
attribute access in a ThreadPoolExecutor with a 5-second wall-clock cap;
on timeout the gate degrades gracefully (returns None, signal proceeds).

v1.10 adds 13F institutional ownership context to every signal:

  13F snapshot     — pulls Ticker.institutional_holders + major_holders
                     via yfinance (7d cache). Reports inst_pct, top-3
                     holders, and Δ-shares vs prior snapshot (QoQ once
                     the cache has accumulated history).
  Retail-heavy gate— if inst_pct < 10 % and the technical verdict is
                     EXECUTE, demote to BUY. Below 10 % means retail bag-
                     holders dominate; technicals are less reliable.

v1.9 fixes three gaps surfaced by the head-to-head vs femisaalpha_runner v3.8
(2026-05-18, 9-ticker scan TSLA NVDA AMD MU GOOGL AAOI VPG HIMS HOOD):

  EARNINGS_BLOCK   — v3.8 caught NVDA entering 2 days before earnings;
                     v1.8.2 had no calendar gate. v1.9 fetches earnings_dte
                     via yfinance.Ticker.calendar (24h cache) and demotes any
                     signal with dte<=5 to WATCH.
  PARABOLIC cap    — backtest showed PARABOLIC_BLOCK is fat-tailed (high
                     avg_ret, ~52% WR). v3.8 sees the same exhaustion and
                     HOLDs; we were sizing it as EXECUTE at EV=9.2. v1.9
                     caps PARABOLIC_BLOCK verdict at BUY regardless of EV.
  BENEISH_RISK     — v3.8 caught HOOD at M=27.32 (extreme accruals); v1.8.2
                     has no fundamental layer. v1.9 computes Beneish M-Score
                     from yfinance financials (30d cache) and demotes any
                     signal where M > -1.78.
Connects to IBKR, pulls market data, applies flag logic, ranks signals.
Usage: python3 femisagent.py [--tickers TSLA NVDA ...] [--portfolio]

v1.1: Supabase calibration hot-load.
v1.2: lazy ib_insync; print_report KeyError fix.
v1.3: thresholds 10/5/0.
v1.4: excess_ret scoring; thresholds 4/2/0.
v1.8.2 — win-rate lift: add RSI, ATR, trend-quality, parabolic-with-trend,
       and multi-confluence meta-flags.

       Diagnosis: id=8 lifted WR only +0.4pp over baseline (60.2% vs 59.8%);
       most of the system's edge was avg-return, not consistency. The
       PARABOLIC family is the worst WR offender (52% on +21% avg_ret —
       fat-tailed). v1.8.2 adds:

         RSI_REVERSAL       — oversold cross (RSI<30 → RSI≥30 + green bar).
                              Classic high-WR pattern in trending universes.
         RSI_OVERBOUGHT     — RSI>75 + recent run-up. Expected AVOID warning.
         ATR_BREAKOUT       — 20d-high breakout that exceeds prior high by
                              1.5+ ATR. Filters marginal breakouts.
         TREND_QUALITY      — price > ma50 AND ma20 > ma50. Confluence
                              overlay (high WR by definition).
         PARABOLIC_TRENDED  — parabolic only when established uptrend
                              already exists. Should lift the PARABOLIC
                              family's 52% WR by filtering dead cats.
         MULTI_CONFLUENCE_3 — at least 3 concurrent bullish flags.
         MULTI_CONFLUENCE_5 — at least 5 concurrent bullish flags.

       Both confluence flags should have very high WR; they're explicit
       "multiple independent confirmations" markers and will be the
       cleanest input to position sizing.

v1.5: A/B'd HRT_STRONG_v2 / VPIN_PERSISTENT / SQUEEZE_PRE_BREAKOUT — all lost.
v1.6: A/B'd HRT_STRONG_v3 / VPIN_PERSISTENT (still) / SQUEEZE_PRE_BREAKOUT (still)
      / OBV_BULL/BEAR / SQUEEZE_BULLISH/BEARISH. Mixed results.

v1.7 — first cull + retry round:

  Dropped (failed A/B over two cycles):
    HRT_STRONG_v2, HRT_STRONG_v3, VPIN_PERSISTENT, VPIN_ELEVATED,
    SQUEEZE_EXTREME, SQUEEZE_PRE_BREAKOUT, SQUEEZE_BEARISH,
    OBV_BULL_DIVERGENCE.

  Kept from v1.6 wins:
    SQUEEZE_BULLISH (+18% bull excess but Wilson-demoted on WR alone),
    OBV_BEAR_DIVERGENCE (regime-flips: -3.3% bull / +4.2% bear).

  New variants (6) replacing the failures:
    HRT_STRONG_v4         — like v3 but no MOMENTUM_CONT exclusion;
                            allows co-firing as conviction overlay.
    VPIN_THRUST           — vpin>0.65 + ret_1d>1% + uptrend + vol>1.5;
                            "explosive buying with confirming flow."
    SQUEEZE_RESOLVING_BULL  — squeeze just broke (yesterday yes, today no)
                            + ret_1d > 1% + vol surge. Catches breakout.
    SQUEEZE_RESOLVING_DOWN  — same transition, with ret_1d < -1%.
                            Named "DOWN" (not "BEAR") because v1.7.2
                            backtest showed +7.94% bull / +2.99% bear
                            excess — i.e., even a small down-day after
                            squeeze break still predicts POSITIVE forward
                            returns. The squeeze-release itself is
                            bullish in this universe regardless of which
                            way the first bar resolves.
    OBV_THRUST            — obv_norm>0.30 + ret_20d>5% + ret_5d>0;
                            concurrent momentum across price + flow.
    PARABOLIC_RECOVERY    — parabolic AND SPY ret_60d > 0; isolates the
                            2009-style / 2023+ recovery sub-regime where
                            PARABOLIC_BLOCK has its strongest edge.
    PARABOLIC_CRISIS      — parabolic AND SPY ret_60d <= 0 AND
                            (OBV_THRUST OR HRT_STRONG_v4). Raw form
                            (no guard) had bimodal distribution: 2022
                            fires lost -12.68%, 2025 fires +53.68%.
                            v1.7.4 guard requires concurrent volume or
                            sustained-uptrend confirmation to filter the
                            failed-momentum half.

  Backtest v1.4 also fixes the Wilson-CI demote rule: was demoting on
  Wilson_lower<0.5 alone, killing fat-tail-positive flags like
  SQUEEZE_BULLISH (54% WR, +30% avg_ret, +18% excess). Now requires
  BOTH Wilson_lower<0.5 AND excess_ret<0 — only demotes signals that
  are actually unprofitable.

compute_flags now accepts spy_ret_20d, spy_ret_60d, spy_60d_dd_pct as
optional regime-context params. PARABOLIC_RECOVERY uses spy_60d_dd_pct.
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

# v1.8.2: bridge upgraded to streamable-HTTP MCP transport which requires
# Mcp-Session-Id header on every tools/call. Get it from initialize.
_BRIDGE_SESSION = None

def _bridge_session():
    """Lazy-handshake the bridge and cache the session ID for the rest of
    this process. Returns None on failure; callers should degrade gracefully."""
    global _BRIDGE_SESSION
    if _BRIDGE_SESSION is not None:
        return _BRIDGE_SESSION
    init = json.dumps({
        "jsonrpc": "2.0", "id": 0, "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "femisagent", "version": "1.7.6"},
        },
    }).encode()
    req = _urllib_req.Request(
        f"{BRIDGE_URL}/mcp", data=init, method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {BRIDGE_TOKEN}",
            "Accept": "application/json, text/event-stream",
        },
    )
    try:
        with _urllib_req.urlopen(req, timeout=10) as r:
            sid = r.headers.get("Mcp-Session-Id") or r.headers.get("mcp-session-id")
            r.read()  # drain
        if sid:
            _BRIDGE_SESSION = sid
            # Send required "initialized" notification (MCP spec) so server
            # transitions out of init state and accepts subsequent calls.
            try:
                notify = json.dumps({
                    "jsonrpc": "2.0", "method": "notifications/initialized"
                }).encode()
                nreq = _urllib_req.Request(
                    f"{BRIDGE_URL}/mcp", data=notify, method="POST",
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {BRIDGE_TOKEN}",
                        "Mcp-Session-Id": sid,
                        "Accept": "application/json, text/event-stream",
                    },
                )
                _urllib_req.urlopen(nreq, timeout=5).read()
            except Exception:
                pass  # notification is best-effort
    except Exception as e:
        print(f"[femisagent] WARN: bridge MCP init failed: {e}")
    return _BRIDGE_SESSION


def fetch_bars_via_bridge(symbol):
    """Fetch bars from Jarvis bridge instead of direct IBKR connection.

    v1.8.2: now does the MCP session handshake (initialize → cache
    Mcp-Session-Id → use it on every tools/call). The bridge upgraded
    to streamable-HTTP transport mid-session; older one-shot calls now
    return 400 "Missing session ID".
    """
    session = _bridge_session()
    payload = json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "fetch_bars", "arguments": {"symbol": symbol}},
    }).encode()
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {BRIDGE_TOKEN}",
        "Accept": "application/json, text/event-stream",
    }
    if session:
        headers["Mcp-Session-Id"] = session
    req = _urllib_req.Request(
        f"{BRIDGE_URL}/mcp", data=payload, headers=headers, method="POST"
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

def _fetch_bars_via_yfinance(symbol, days=90):
    """v2.1 fallback when bridge fetch_bars returns []. yfinance daily bars,
    wrapped in the same Bar shape as the bridge path. Returns [] on any
    failure (no network, ticker invalid, yfinance not installed).
    """
    yf = _yf()
    if yf is None:
        return []
    try:
        df = _yf_call(lambda: yf.Ticker(symbol).history(period=f"{days}d", auto_adjust=False))
        if df is None or (hasattr(df, "empty") and df.empty):
            return []
        bars = []
        for ts, row in df.iterrows():
            try:
                bars.append(make_bar_obj({
                    "date": ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts,
                    "open": float(row["Open"]),
                    "high": float(row["High"]),
                    "low": float(row["Low"]),
                    "close": float(row["Close"]),
                    "volume": float(row["Volume"]),
                }))
            except (KeyError, ValueError):
                continue
        return bars
    except Exception:
        return []


def get_positions_via_bridge():
    """Fetch portfolio positions via the bridge's get_positions MCP tool.

    Returns a list of (symbol, exchange, currency, qty, avgCost) tuples
    matching the shape of get_portfolio_tickers() so the caller code in
    run() is symmetric across bridge/direct paths.

    v1.8.2: shipped to fix the long-standing --portfolio crash in bridge
    mode. Previously the script called ib.reqPositionsAsync() on an
    unconnected IB() client (bridge mode skips the IBKR handshake), so
    --portfolio always raised ConnectionError. Now portfolio fetch goes
    through the same MCP session that fetch_bars uses.
    """
    session = _bridge_session()
    payload = json.dumps({
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {"name": "get_positions", "arguments": {}},
    }).encode()
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {BRIDGE_TOKEN}",
        "Accept": "application/json, text/event-stream",
    }
    if session:
        headers["Mcp-Session-Id"] = session
    req = _urllib_req.Request(
        f"{BRIDGE_URL}/mcp", data=payload, headers=headers, method="POST"
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
                    positions_data = json.loads(content[0]["text"])
                    out = []
                    for pos in positions_data:
                        sym = pos.get("symbol", "")
                        exch = pos.get("exchange", "SMART")
                        cur = pos.get("currency", "USD")
                        # v2.3.3: bridge MCP sometimes returns numeric fields as
                        # JSON strings — coerce defensively to avoid str/float
                        # arithmetic errors downstream in the run loop.
                        try:
                            qty = float(pos.get("qty") or pos.get("position") or 0)
                        except (TypeError, ValueError):
                            qty = 0.0
                        try:
                            cost = float(pos.get("avgCost") or pos.get("avg_cost") or 0)
                        except (TypeError, ValueError):
                            cost = 0.0
                        if sym:
                            out.append((sym, exch, cur, qty, cost))
                    return out
    except Exception as e:
        print(f"[femisagent] WARN: bridge get_positions failed: {e}")
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

# v1.8.2 indicator helpers
def _rsi(closes, period=14):
    """Wilder's RSI. Returns 50 for insufficient data."""
    if len(closes) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i-1]
        gains.append(max(diff, 0.0))
        losses.append(max(-diff, 0.0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)

def _rsi_series(closes, period=14):
    """Wilder's RSI series — returns one value per bar (None for first `period`).
    Needed by divergence detectors that compare past RSI values."""
    n = len(closes)
    if n < period + 1:
        return [None] * n
    series = [None] * n
    gains, losses = [], []
    for i in range(1, n):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0.0))
        losses.append(max(-diff, 0.0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    series[period] = 100.0 if avg_loss == 0 else 100.0 - 100.0 / (1.0 + (avg_gain / avg_loss))
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0:
            series[i + 1] = 100.0
        else:
            rs = avg_gain / avg_loss
            series[i + 1] = 100.0 - 100.0 / (1.0 + rs)
    return series

def _atr(highs, lows, closes, period=14):
    """Wilder's ATR. Returns 0 for insufficient data."""
    if len(closes) < period + 1:
        return 0.0
    trs = []
    for i in range(1, len(closes)):
        trs.append(max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i-1]),
            abs(lows[i] - closes[i-1]),
        ))
    if len(trs) < period:
        return sum(trs) / len(trs) if trs else 0.0
    atr = sum(trs[:period]) / period
    for tr in trs[period:]:
        atr = (atr * (period - 1) + tr) / period
    return atr

# v2.4 — EMA series + rolling stdev (needed for the Ceyhun OBOB Pine port)
def _ema_series(values, period):
    """EMA of a series; first period-1 entries None, seeds with SMA. Matches Pine."""
    n = len(values)
    if n < period:
        return [None] * n
    out = [None] * (period - 1)
    sma = sum(values[:period]) / period
    out.append(sma)
    alpha = 2.0 / (period + 1)
    prev = sma
    for v in values[period:]:
        prev = prev + alpha * (v - prev)
        out.append(prev)
    return out

def _stdev_rolling(values, period):
    """Rolling stdev over trailing `period`; first period-1 entries None."""
    out = []
    for i in range(len(values)):
        if i < period - 1:
            out.append(None)
            continue
        window = values[i - period + 1 : i + 1]
        mean = sum(window) / period
        var = sum((x - mean) ** 2 for x in window) / period
        out.append(var ** 0.5 if var > 0 else 0.0)
    return out

def ceyhun_obob_signal(bars, n=5):
    """Ceyhun Overbought/Oversold (Pine v4) — triple-EMA-smoothed z-score with
    crossover trigger. Source: ceyhun, Mozilla Public License 2.0.

      ys1 = (high + low + close*2) / 4
      rk5 = (ys1 - ema(ys1, n)) * 100 / stdev(ys1, n)
      up  = ema(ema(rk5, n), n)
      down = ema(up, n)
      Buy  = up crossover  down
      Sell = up crossunder down

    Returns 'BUY' / 'SELL' / None for the most recent bar.
    """
    if len(bars) < n * 4 + 2:
        return None
    ys1 = [(b.high + b.low + b.close * 2) / 4.0 for b in bars]
    rk3 = _ema_series(ys1, n)
    rk4 = _stdev_rolling(ys1, n)
    rk5 = []
    for v, e, s in zip(ys1, rk3, rk4):
        if e is None or s is None or s == 0:
            rk5.append(None)
        else:
            rk5.append((v - e) * 100.0 / s)
    rk5_clean = [v for v in rk5 if v is not None]
    if len(rk5_clean) < n * 3 + 2:
        return None
    rk6 = _ema_series(rk5_clean, n)
    rk6_clean = [v for v in rk6 if v is not None]
    if len(rk6_clean) < n * 2 + 2:
        return None
    up = _ema_series(rk6_clean, n)
    up_clean = [v for v in up if v is not None]
    if len(up_clean) < n + 2:
        return None
    down = _ema_series(up_clean, n)
    aligned = [(u, d) for u, d in zip(up_clean, down) if u is not None and d is not None]
    if len(aligned) < 2:
        return None
    u_prev, d_prev = aligned[-2]
    u_curr, d_curr = aligned[-1]
    if u_prev <= d_prev and u_curr > d_curr:
        return "BUY"
    if u_prev >= d_prev and u_curr < d_curr:
        return "SELL"
    return None

# v2.5 — Parabolic SAR (Wilder, 1978) + Trend Trader-Remastered entry signal
def _psar(highs, lows, start=0.02, increment=0.02, max_af=0.025):
    """Parabolic SAR. Returns list same length as input; first value is None.

    Note: the default max_af here is 0.025 (not 0.2) to match the TTR Pine
    indicator's 'lagging' PSAR variant. Standard PSAR uses max_af=0.2.
    """
    n = len(highs)
    if n < 3:
        return [None] * n
    psar = [None] * n
    # Initial trend guess: compare first two bars
    is_long = highs[1] >= highs[0]
    af = start
    ep = highs[1] if is_long else lows[1]
    psar[1] = lows[0] if is_long else highs[0]
    for i in range(2, n):
        prev_psar = psar[i - 1]
        if is_long:
            new_psar = prev_psar + af * (ep - prev_psar)
            # Constrain: PSAR can't exceed prior two bars' lows
            new_psar = min(new_psar, lows[i - 1], lows[i - 2])
            if highs[i] > ep:
                ep = highs[i]
                af = min(af + increment, max_af)
            if lows[i] < new_psar:
                # Trend reversal — flip to short, reset
                is_long = False
                psar[i] = ep
                ep = lows[i]
                af = start
            else:
                psar[i] = new_psar
        else:
            new_psar = prev_psar + af * (ep - prev_psar)
            new_psar = max(new_psar, highs[i - 1], highs[i - 2])
            if lows[i] < ep:
                ep = lows[i]
                af = min(af + increment, max_af)
            if highs[i] > new_psar:
                is_long = True
                psar[i] = ep
                ep = highs[i]
                af = start
            else:
                psar[i] = new_psar
    return psar

def ttr_signal(bars, start=0.02, increment=0.02, max_af=0.025):
    """Trend Trader-Remastered (Pine v6, aybarsm) — entry-only port.

    Detects PSAR crossover events on the latest bar:
      BUY  = prior PSAR was above prior high AND current high crosses above PSAR
      SELL = prior PSAR was below prior low  AND current low  crosses below PSAR

    The full Pine indicator also emits TP (take-profit at Bill Williams
    fractal breakouts) and RE (re-entry at minimum proximity to PSAR)
    signals, but those require a stateful position tracker across bars
    that is out of scope for a daily-snapshot scanner. The entry-only
    signal captures the primary trade trigger.

    Default params (0.02, 0.02, 0.025) match the original Pine — the
    unusual low max=0.025 (vs standard 0.2) makes this the 'lagging' PSAR
    variant that's the foundation of the TTR system.
    """
    if len(bars) < 5:
        return None
    highs = [b.high for b in bars]
    lows = [b.low for b in bars]
    psar = _psar(highs, lows, start=start, increment=increment, max_af=max_af)
    if psar[-1] is None or psar[-2] is None:
        return None
    p_prev, p_curr = psar[-2], psar[-1]
    h_prev, h_curr = highs[-2], highs[-1]
    l_prev, l_curr = lows[-2], lows[-1]
    if p_prev > h_prev and h_curr > p_curr:
        return "BUY"
    if p_prev < l_prev and l_curr < p_curr:
        return "SELL"
    return None

def ttr_near_flip(bars, threshold_pct=1.0, start=0.02, increment=0.02, max_af=0.025):
    """v2.5.1: setup detector — fires when PSAR is within `threshold_pct` of
    today's close, signaling a likely TTR crossover on the next bar.

    Returns:
      'BULLISH_NEAR' — PSAR is above price but within threshold (close to flipping
                       BULLISH, i.e. price about to break above PSAR → TTR_BUY)
      'BEARISH_NEAR' — PSAR is below price but within threshold (close to flipping
                       BEARISH, i.e. price about to break below PSAR → TTR_SELL)
      None            — PSAR is far from price OR currently at the flip
                        (use ttr_signal for the actual crossover bar).
    """
    if len(bars) < 5:
        return None
    highs = [b.high for b in bars]
    lows = [b.low for b in bars]
    psar = _psar(highs, lows, start=start, increment=increment, max_af=max_af)
    if psar[-1] is None:
        return None
    p = psar[-1]
    c = bars[-1].close
    if c <= 0:
        return None
    distance_pct = abs(p - c) / c * 100.0
    if distance_pct > threshold_pct:
        return None
    if p > c:
        return "BULLISH_NEAR"   # price below PSAR, about to break above
    if p < c:
        return "BEARISH_NEAR"   # price above PSAR, about to break below
    return None

def libertus_rsi_div_signal(bars, length=14, xbars=90, pivot_window=3, recency_bars=10):
    """Libertus RSI Divergence (Pine v4 port, Libertus 2021) — detects
    bullish/bearish divergence between 14-period RSI and price.

    v2.6.1: pivot-based detection (matches Pine's `highestbars` semantics
    more closely than the original window-MAX comparison).

    Algorithm:
      1. Find swing-high pivots in the last `xbars` bars (a swing high
         is a close higher than the `pivot_window` bars on each side).
      2. Compare the two most recent swing highs:
           if newer pivot price > older pivot price AND
              newer pivot RSI < older pivot RSI AND
              newer pivot is within `recency_bars` of current bar
           → BEAR_DIV
      3. Same logic for swing lows → BULL_DIV.

    pivot_window=3 means a confirmed swing pivot needs 3 lower bars on
    each side. Most recent visible pivot is at least 3 bars old.
    recency_bars=10 limits the signal to "fresh" divergences only.

    Returns 'BULL_DIV', 'BEAR_DIV', or None.
    """
    if len(bars) < length + 20:
        return None
    n = len(bars)
    closes = [b.close for b in bars]
    rsi_s = _rsi_series(closes, length)

    # Build pivot lists in the lookback window
    start = max(length, n - xbars) + pivot_window
    end = n - pivot_window
    pivot_highs = []  # list of (idx, close, rsi)
    pivot_lows = []
    for i in range(start, end):
        if rsi_s[i] is None:
            continue
        left = closes[i - pivot_window : i]
        right = closes[i + 1 : i + 1 + pivot_window]
        if not left or not right:
            continue
        cmax_neighbors = max(left + right)
        cmin_neighbors = min(left + right)
        if closes[i] > cmax_neighbors:
            pivot_highs.append((i, closes[i], rsi_s[i]))
        if closes[i] < cmin_neighbors:
            pivot_lows.append((i, closes[i], rsi_s[i]))

    # Bearish divergence: most recent swing high has higher price but lower RSI
    if len(pivot_highs) >= 2:
        prev_i, prev_c, prev_r = pivot_highs[-2]
        curr_i, curr_c, curr_r = pivot_highs[-1]
        if (curr_c > prev_c and curr_r < prev_r
                and curr_i - prev_i >= 5
                and n - curr_i <= recency_bars):
            return "BEAR_DIV"
    # Bullish divergence: most recent swing low has lower price but higher RSI
    if len(pivot_lows) >= 2:
        prev_i, prev_c, prev_r = pivot_lows[-2]
        curr_i, curr_c, curr_r = pivot_lows[-1]
        if (curr_c < prev_c and curr_r > prev_r
                and curr_i - prev_i >= 5
                and n - curr_i <= recency_bars):
            return "BULL_DIV"
    return None

# ── v1.9 fundamentals (earnings calendar + Beneish M-Score) ────────────────

_FUND_CACHE_PATH = "/data/.openclaw/workspace/memory/femisagent_fundamentals.cache.json"
_FUND_CACHE = None

# v1.10.1: yfinance has no per-request timeout. Wrap attribute access in a
# thread with a hard wall-clock cap so a single hung HTTP call can't freeze
# the entire scan. 5s/call × 5 calls/ticker × 69 tickers worst case ~= 29min,
# but in practice the cache absorbs most of that after the first run.
import concurrent.futures as _cf
_YF_EXECUTOR = _cf.ThreadPoolExecutor(max_workers=4, thread_name_prefix="yf")
_YF_TIMEOUT_SEC = 5

def _yf_call(fn):
    """Run `fn` (a zero-arg callable) with a hard timeout. Returns None on
    timeout, exception, or rate-limit. Safe to chain attribute accesses by
    wrapping each in a lambda: _yf_call(lambda: yf.Ticker(s).calendar)."""
    try:
        return _YF_EXECUTOR.submit(fn).result(timeout=_YF_TIMEOUT_SEC)
    except (_cf.TimeoutError, Exception):
        return None

def _load_fund_cache():
    global _FUND_CACHE
    if _FUND_CACHE is not None:
        return _FUND_CACHE
    try:
        with open(_FUND_CACHE_PATH) as f:
            _FUND_CACHE = json.load(f)
    except Exception:
        _FUND_CACHE = {}
    return _FUND_CACHE

def _save_fund_cache():
    if _FUND_CACHE is None:
        return
    try:
        os.makedirs(os.path.dirname(_FUND_CACHE_PATH), exist_ok=True)
        with open(_FUND_CACHE_PATH, "w") as f:
            json.dump(_FUND_CACHE, f, indent=2)
    except Exception:
        pass

def _yf():
    """Lazy yfinance import. Returns None if not installed."""
    try:
        import yfinance as yf
        return yf
    except ImportError:
        return None

def get_earnings_dte(symbol):
    """Days to next earnings via yfinance. 24h cache. None if unknown."""
    cache = _load_fund_cache()
    entry = cache.setdefault(symbol, {})
    now_ts = datetime.now().timestamp()
    if entry.get("earnings_fetched_at", 0) > now_ts - 86400:
        return entry.get("earnings_dte")
    yf = _yf()
    if yf is None:
        return None
    dte = None
    try:
        cal = _yf_call(lambda: yf.Ticker(symbol).calendar)
        ed = None
        if isinstance(cal, dict):
            v = cal.get("Earnings Date")
            ed = v[0] if isinstance(v, list) and v else v
        elif cal is not None and hasattr(cal, "empty") and not cal.empty:
            ed = cal.iloc[0, 0]
        if ed is not None:
            if hasattr(ed, "to_pydatetime"):
                ed = ed.to_pydatetime()
            if hasattr(ed, "year") and not isinstance(ed, datetime):
                ed = datetime(ed.year, ed.month, ed.day)
            if isinstance(ed, str):
                ed = datetime.fromisoformat(ed[:10])
            if isinstance(ed, datetime):
                dte = (ed - datetime.now()).days
    except Exception:
        pass
    entry["earnings_dte"] = dte
    entry["earnings_fetched_at"] = now_ts
    _save_fund_cache()
    return dte

def get_beneish_m(symbol):
    """Beneish M-Score from yfinance financials. 30d cache. None if data insufficient.
    M > -1.78 = elevated manipulation/accruals risk."""
    cache = _load_fund_cache()
    entry = cache.setdefault(symbol, {})
    now_ts = datetime.now().timestamp()
    if entry.get("beneish_fetched_at", 0) > now_ts - 30 * 86400:
        return entry.get("beneish_m")
    yf = _yf()
    if yf is None:
        return None
    m_score = None
    try:
        t = yf.Ticker(symbol)
        bs = _yf_call(lambda: t.balance_sheet)
        is_ = _yf_call(lambda: t.income_stmt)
        cf = _yf_call(lambda: t.cashflow)
        if bs is None or is_ is None or cf is None:
            raise ValueError("missing statements")
        if bs.shape[1] < 2 or is_.shape[1] < 2 or cf.shape[1] < 2:
            raise ValueError("need 2yr data")

        def pick(df, *keys):
            for k in keys:
                if k in df.index:
                    return float(df.loc[k].iloc[0]), float(df.loc[k].iloc[1])
            return None, None

        rec_t, rec_p = pick(bs, "Receivables", "Accounts Receivable")
        sales_t, sales_p = pick(is_, "Total Revenue", "Operating Revenue")
        cogs_t, cogs_p = pick(is_, "Cost Of Revenue", "Cost Of Goods Sold")
        ca_t, ca_p = pick(bs, "Current Assets", "Total Current Assets")
        ppe_t, ppe_p = pick(bs, "Net PPE", "Property Plant Equipment Net")
        ta_t, ta_p = pick(bs, "Total Assets")
        dep_t, dep_p = pick(cf, "Depreciation And Amortization", "Depreciation")
        sga_t, sga_p = pick(is_, "Selling General And Administration", "Selling General Administrative")
        ni_t, ni_p = pick(is_, "Net Income", "Net Income Common Stockholders")
        ocf_t, _ = pick(cf, "Operating Cash Flow", "Cash Flow From Continuing Operating Activities")
        tl_t, tl_p = pick(bs, "Total Liabilities Net Minority Interest", "Total Liab")

        req = [rec_t, rec_p, sales_t, sales_p, cogs_t, cogs_p,
               ca_t, ca_p, ppe_t, ppe_p, ta_t, ta_p,
               sga_t, sga_p, ni_t, ni_p, tl_t, tl_p]
        if any(v is None or v == 0 for v in [sales_t, sales_p, ta_t, ta_p, rec_p]):
            raise ValueError("zero denominator")
        if any(v is None for v in req):
            raise ValueError("missing field")

        DSRI = (rec_t / sales_t) / (rec_p / sales_p)
        GMI  = ((sales_p - cogs_p) / sales_p) / ((sales_t - cogs_t) / sales_t)
        AQI  = (1 - (ca_t + ppe_t) / ta_t) / (1 - (ca_p + ppe_p) / ta_p)
        SGI  = sales_t / sales_p
        DEPI = ((dep_p / (dep_p + ppe_p)) / (dep_t / (dep_t + ppe_t))
                if dep_t and dep_p and (dep_p + ppe_p) and (dep_t + ppe_t) else 1.0)
        SGAI = (sga_t / sales_t) / (sga_p / sales_p)
        LVGI = (tl_t / ta_t) / (tl_p / ta_p)
        TATA = (ni_t - (ocf_t or 0)) / ta_t
        m_score = round(
            -4.84 + 0.92*DSRI + 0.528*GMI + 0.404*AQI + 0.892*SGI
            + 0.115*DEPI - 0.172*SGAI + 4.679*TATA - 0.327*LVGI, 2
        )
    except Exception:
        pass
    entry["beneish_m"] = m_score
    entry["beneish_fetched_at"] = now_ts
    _save_fund_cache()
    return m_score


def get_13f_context(symbol):
    """Snapshot of institutional ownership via yfinance. 7d cache.
    Returns dict {inst_pct, n_top_holders, top_3, qoq_shares_change_pct} or None.

    qoq_shares_change_pct compares aggregate top-10 shares vs the snapshot
    stored when the cache entry was last invalidated (so it lights up only
    after the second-quarter refresh of a given ticker).
    """
    cache = _load_fund_cache()
    entry = cache.setdefault(symbol, {})
    now_ts = datetime.now().timestamp()
    cached = entry.get("13f")
    # v1.10.2: invalidate the cache early if the prior fetch failed to
    # populate inst_pct — gives the upgraded parser a chance to succeed
    # without waiting for the 7-day TTL to elapse.
    cached_has_inst = isinstance(cached, dict) and cached.get("inst_pct") is not None
    if entry.get("13f_fetched_at", 0) > now_ts - 7 * 86400 and cached_has_inst:
        return cached
    yf = _yf()
    if yf is None:
        return None
    result = None
    try:
        t = yf.Ticker(symbol)
        ih = _yf_call(lambda: t.institutional_holders)
        if ih is None or (hasattr(ih, "empty") and ih.empty):
            raise ValueError("no institutional holders")
        top = ih.head(10) if hasattr(ih, "head") else ih
        top_3 = []
        total_shares_now = 0
        for _, row in top.iterrows() if hasattr(top, "iterrows") else []:
            shares = row.get("Shares") or row.get("shares") or 0
            try:
                shares = int(shares)
            except (TypeError, ValueError):
                shares = 0
            total_shares_now += shares
            if len(top_3) < 3:
                holder = row.get("Holder") or row.get("holder") or "?"
                pct = row.get("pctHeld") or row.get("% Out") or row.get("pct_held")
                try:
                    pct = round(float(pct) * 100, 2) if pct and float(pct) < 1 else (round(float(pct), 2) if pct else None)
                except (TypeError, ValueError):
                    pct = None
                top_3.append({"holder": str(holder), "shares": shares, "pct_out": pct})

        inst_pct = None
        try:
            mh = _yf_call(lambda: t.major_holders)
            if mh is not None and not (hasattr(mh, "empty") and mh.empty):
                # New yfinance format (0.2.55+): single-column DataFrame
                # indexed by camelCase field names (institutionsPercentHeld etc).
                try:
                    if hasattr(mh, "index") and "institutionsPercentHeld" in mh.index:
                        v = float(mh.loc["institutionsPercentHeld"].iloc[0])
                        inst_pct = round(v * 100, 2) if 0 < v <= 1 else round(v, 2)
                except Exception:
                    pass
                # Old format fallback: 2-column DataFrame with text labels.
                if inst_pct is None:
                    for idx, row in mh.iterrows():
                        # The label may live in the index OR in row.values.
                        label_parts = [str(idx)] + [str(v) for v in row.values]
                        label = " ".join(label_parts).lower()
                        if "institution" not in label:
                            continue
                        for v in row.values:
                            try:
                                f = float(str(v).rstrip("%").replace(",", ""))
                            except (TypeError, ValueError):
                                continue
                            if 0 < f <= 1:
                                inst_pct = round(f * 100, 2)
                                break
                            if 1 < f <= 100:
                                inst_pct = round(f, 2)
                                break
                        if inst_pct is not None:
                            break
            # Last-resort fallback: Ticker.info has heldPercentInstitutions.
            if inst_pct is None:
                info = _yf_call(lambda: t.info)
                if isinstance(info, dict):
                    v = info.get("heldPercentInstitutions")
                    if v is not None:
                        try:
                            f = float(v)
                            inst_pct = round(f * 100, 2) if 0 < f <= 1 else round(f, 2)
                        except (TypeError, ValueError):
                            pass
        except Exception:
            pass

        qoq = None
        prior = entry.get("13f_shares_snapshot")
        if prior and total_shares_now > 0:
            qoq = round((total_shares_now - prior) / prior * 100, 1)

        result = {
            "inst_pct": inst_pct,
            "n_top_holders": len(top.index) if hasattr(top, "index") else 0,
            "top_3": top_3,
            "total_shares_top10": total_shares_now,
            "qoq_shares_change_pct": qoq,
        }
        entry["13f_shares_snapshot"] = total_shares_now
    except Exception:
        pass
    entry["13f"] = result
    entry["13f_fetched_at"] = now_ts
    _save_fund_cache()
    return result


# ── v2.0 femisapien backend orchestration ───────────────────────────────────

# v2.2: try multiple candidate paths — env override, in-container path,
# and host bind-mount path. First existing wins.
_FEMISAPIEN_PATH_CANDIDATES = [
    os.environ.get("FEMISAPIEN_PATH"),
    "/data/.openclaw/workspace/scripts/femisapien_live_signal.py",
    "/docker/openclaw-vhii/data/.openclaw/workspace/scripts/femisapien_live_signal.py",
]
_FEMISAPIEN_CWD_CANDIDATES = [
    os.environ.get("FEMISAPIEN_CWD"),
    "/data/.openclaw/workspace",
    "/docker/openclaw-vhii/data/.openclaw/workspace",
]
_FEMISAPIEN_TIMEOUT_SEC = 60
_FEMISAPIEN_CACHE_TTL_SEC = 3600

def _resolve_femisapien_path():
    for p in _FEMISAPIEN_PATH_CANDIDATES:
        if p and os.path.exists(p):
            return p
    return None

def _resolve_femisapien_cwd():
    for p in _FEMISAPIEN_CWD_CANDIDATES:
        if p and os.path.isdir(p):
            return p
    return None

def get_femisapien_signals(symbol):
    """Call femisapien_live_signal.py for the rich quant-signal set.

    Returns a dict of normalized fields or None on any failure. Cached 1h
    per ticker in the shared fundamentals cache. Graceful degradation if
    the binary isn't present (running off-bridge), subprocess times out,
    or output isn't JSON-parseable — femisagent's native flag engine
    proceeds either way.
    """
    cache = _load_fund_cache()
    entry = cache.setdefault(symbol, {})
    now_ts = datetime.now().timestamp()
    if entry.get("femisapien_fetched_at", 0) > now_ts - _FEMISAPIEN_CACHE_TTL_SEC:
        return entry.get("femisapien")
    fa_path = _resolve_femisapien_path()
    fa_cwd = _resolve_femisapien_cwd()
    if not fa_path:
        entry["femisapien"] = None
        entry["femisapien_fetched_at"] = now_ts
        return None
    try:
        import subprocess, re as _re
        result = subprocess.run(
            [sys.executable, fa_path, "--ticker", symbol, "--json"],
            capture_output=True, text=True,
            timeout=_FEMISAPIEN_TIMEOUT_SEC,
            cwd=fa_cwd,
        )
        stdout = result.stdout.strip()
        # femisapien wraps output in [...] array — extract first object
        m = _re.search(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", stdout, _re.DOTALL)
        if not m:
            raise ValueError("no JSON object in output")
        data = json.loads(m.group())
        signals = {
            "vpin": data.get("vpin"),
            "vpin_signal": data.get("vpin_signal"),
            "hgdcf_signal": data.get("hgdcf_signal"),
            "hgdcf_score": data.get("hgdcf_score"),
            "momentum_class": data.get("momentum_class"),
            "momentum_exhaustion_label": data.get("momentum_exhaustion_label"),
            "momentum_exhaustion_score": data.get("momentum_exhaustion_score"),
            "technical_signal": data.get("technical_signal"),
            "conviction_pct": data.get("conviction_pct"),
            "final_signal": data.get("final_signal"),
            "rationale": data.get("rationale", "")[:120],
            "cross_asset_gate": data.get("cross_asset_gate"),
            "earnings_block": data.get("earnings_block"),
            "fundamental_block": data.get("fundamental_block"),
            "lppls_bubble": data.get("lppls_bubble"),
            "lppls_crash_prob": data.get("lppls_crash_prob"),
            "garch_beta": data.get("garch_beta"),
            "vol_persistence": data.get("vol_persistence"),
            "vrp_signal": data.get("vrp_signal"),
            "pead_signal": data.get("pead_signal"),
            "altman_signal": data.get("altman_signal"),
            "beneish_signal": data.get("beneish_signal"),
            "dispersion_signal": data.get("dispersion_signal"),
            "pin_signal": data.get("pin_signal"),
            "distortion_score": data.get("distortion_score"),
            "distortion_signal": data.get("distortion_signal"),
            "regime": data.get("regime"),
            "kelly_adjusted": data.get("kelly_adjusted"),
            "position_size_pct": data.get("position_size_pct"),
        }
        entry["femisapien"] = signals
        entry["femisapien_fetched_at"] = now_ts
        _save_fund_cache()
        return signals
    except Exception:
        entry["femisapien"] = None
        entry["femisapien_fetched_at"] = now_ts
        return None


# ── v2.3 backends: TA Fusion + sentiment (news_radar + analyst_intelligence) ──

_TA_FUSION_PATH_CANDIDATES = [
    os.environ.get("TA_FUSION_PATH"),
    "/data/.openclaw/workspace/scripts/ta_fusion_consolidated.py",
    "/docker/openclaw-vhii/data/.openclaw/workspace/scripts/ta_fusion_consolidated.py",
]
_NEWS_RADAR_PATH_CANDIDATES = [
    os.environ.get("NEWS_RADAR_PATH"),
    "/data/.openclaw/workspace/scripts/news_radar.py",
    "/docker/openclaw-vhii/data/.openclaw/workspace/scripts/news_radar.py",
]
_ANALYST_INT_PATH_CANDIDATES = [
    os.environ.get("ANALYST_INT_PATH"),
    "/data/.openclaw/workspace/scripts/analyst_intelligence.py",
    "/docker/openclaw-vhii/data/.openclaw/workspace/scripts/analyst_intelligence.py",
]
_PRICE_DECOMP_PATH_CANDIDATES = [
    os.environ.get("PRICE_DECOMP_PATH"),
    "/data/.openclaw/workspace/scripts/epv_sotp.py",
    "/data/.openclaw/workspace/scripts/football_field.py",
    "/data/.openclaw/workspace/scripts/valuation_zscore.py",
    "/docker/openclaw-vhii/data/.openclaw/workspace/scripts/epv_sotp.py",
    "/docker/openclaw-vhii/data/.openclaw/workspace/scripts/football_field.py",
    "/docker/openclaw-vhii/data/.openclaw/workspace/scripts/valuation_zscore.py",
]

# v2.7 — three new backends: insider_radar, earnings_revision_tracker, options_flow
_INSIDER_PATH_CANDIDATES = [
    os.environ.get("INSIDER_RADAR_PATH"),
    "/data/.openclaw/workspace/scripts/insider_radar.py",
    "/docker/openclaw-vhii/data/.openclaw/workspace/scripts/insider_radar.py",
]
_EPS_REV_PATH_CANDIDATES = [
    os.environ.get("EPS_REV_PATH"),
    "/data/.openclaw/workspace/scripts/earnings_revision_tracker.py",
    "/docker/openclaw-vhii/data/.openclaw/workspace/scripts/earnings_revision_tracker.py",
]
_OPTIONS_FLOW_PATH_CANDIDATES = [
    os.environ.get("OPTIONS_FLOW_PATH"),
    "/data/.openclaw/workspace/scripts/options_flow.py",
    "/docker/openclaw-vhii/data/.openclaw/workspace/scripts/options_flow.py",
]

# v2.8 — three new Tier-1 per-ticker signal backends
_CONGRESS_PATH_CANDIDATES = [
    os.environ.get("CONGRESS_PATH"),
    "/data/.openclaw/workspace/scripts/congressional_signal.py",
    "/docker/openclaw-vhii/data/.openclaw/workspace/scripts/congressional_signal.py",
]
_SHORT_INT_PATH_CANDIDATES = [
    os.environ.get("SHORT_INTEREST_PATH"),
    "/data/.openclaw/workspace/scripts/short_interest_monitor.py",
    "/docker/openclaw-vhii/data/.openclaw/workspace/scripts/short_interest_monitor.py",
]
_VOL_ANOMALY_PATH_CANDIDATES = [
    os.environ.get("VOL_ANOMALY_PATH"),
    "/data/.openclaw/workspace/scripts/volume_anomaly_monitor.py",
    "/docker/openclaw-vhii/data/.openclaw/workspace/scripts/volume_anomaly_monitor.py",
]
_ADAPTIVE_PRED_PATH_CANDIDATES = [
    os.environ.get("ADAPTIVE_PRED_PATH"),
    "/data/.openclaw/workspace/scripts/adaptive_predictor.py",
    "/docker/openclaw-vhii/data/.openclaw/workspace/scripts/adaptive_predictor.py",
]

def _first_existing(paths):
    for p in paths:
        if p and os.path.exists(p):
            return p
    return None

def _backend_cache_get(symbol, backend_name, ttl_sec):
    """Returns (cached_value, should_refetch). Doesn't return None as cached."""
    cache = _load_fund_cache()
    entry = cache.setdefault(symbol, {})
    fetched_at = entry.get(f"{backend_name}_fetched_at", 0)
    now_ts = datetime.now().timestamp()
    if fetched_at > now_ts - ttl_sec:
        cached = entry.get(backend_name)
        if cached is not None:
            return cached, False
    return None, True

def _backend_cache_set(symbol, backend_name, value):
    cache = _load_fund_cache()
    entry = cache.setdefault(symbol, {})
    entry[backend_name] = value
    entry[f"{backend_name}_fetched_at"] = datetime.now().timestamp()
    # v2.3: only persist if value is not None — avoid poisoning the cache
    if value is not None:
        _save_fund_cache()

def get_ta_fusion_signals(symbol):
    """Run ta_fusion_consolidated.py --ticker SYM and parse its text report.
    Returns dict with parsed indicators, or None. 1h cache."""
    cached, refetch = _backend_cache_get(symbol, "ta_fusion", 3600)
    if not refetch:
        return cached
    path = _first_existing(_TA_FUSION_PATH_CANDIDATES)
    if not path:
        return None
    try:
        import subprocess, re as _re
        result = subprocess.run(
            [sys.executable, path, "--ticker", symbol],
            capture_output=True, text=True, timeout=45,
            cwd=os.path.dirname(path),
        )
        out = result.stdout
        if not out:
            return None
        signals = {}
        for key, pat in [
            ("core_signal", r"Core signal\s*:\s*(\S+)"),
            ("conviction_pct", r"Conviction\s*:\s*([\d.]+)%"),
            ("executable", r"Executable\s*:\s*(\S+)"),
            ("ema_ribbon", r"EMA ribbon\s*:\s*(\S+)"),
            ("dema", r"DEMA\s*:\s*(\S+)"),
            ("obv_trend", r"OBV trend\s*:\s*(\S+)"),
            ("obv_div", r"OBV trend\s*:\s*\S+\s*\|\s*div:\s*(\S+)"),
            ("cmf_signal", r"CMF\s*:\s*[-\d.]+\s*→\s*(\S+)"),
            ("hl_cycle", r"HL cycle\s*:\s*([\d.]+)\s*bars\s*→\s*(\S+)"),
            ("williams_signal", r"Williams%R\s*:\s*[-\d.]+\s*→\s*(\S+)"),
            ("cci_signal", r"CCI\s*:\s*[-\d.]+\s*→\s*(\S+)"),
            ("vol_rsi_signal", r"Volume RSI\s*:\s*[-\d.]+\s*→\s*(\S+)"),
            ("supplementary_consensus", r"Supplementary consensus:\s*([+-]?[\d.]+)"),
        ]:
            m = _re.search(pat, out)
            if m:
                if key == "hl_cycle":
                    signals["hl_cycle_bars"] = float(m.group(1))
                    signals["hl_cycle_phase"] = m.group(2)
                elif key in ("conviction_pct", "supplementary_consensus"):
                    try: signals[key] = float(m.group(1))
                    except ValueError: pass
                else:
                    signals[key] = m.group(1)
        _backend_cache_set(symbol, "ta_fusion", signals)
        return signals
    except Exception:
        return None

def get_sentiment_signals(symbol):
    """Combined sentiment from news_radar.py + analyst_intelligence.py.
    Returns dict with news_score, analyst_score, combined_score (-1..+1) or None.
    Cached 6h since news/analyst views move slowly."""
    cached, refetch = _backend_cache_get(symbol, "sentiment", 6 * 3600)
    if not refetch:
        return cached
    out = {"news_score": None, "analyst_score": None, "combined_score": None,
           "news_raw": None, "analyst_raw": None}

    # news_radar — uses --ticker SYM, 24h window, parse emoji sentiment
    news_path = _first_existing(_NEWS_RADAR_PATH_CANDIDATES)
    if news_path:
        try:
            import subprocess, re as _re
            cwd = os.path.dirname(news_path)
            r = subprocess.run(
                [sys.executable, news_path, "--ticker", symbol, "--timeframe", "24"],
                capture_output=True, text=True, timeout=45, cwd=cwd,
            )
            t = (r.stdout or "")[:8000]
            out["news_raw"] = t[:300]
            # Count emoji + media-marker pairs per headline (not the legend line)
            # 🟢/🔴/⚪ followed by any media-type emoji (📰/📄/📝/🔍/🏦/💬)
            bull = len(_re.findall(r"🟢[📰📄📝🔍🏦💬]", t))
            bear = len(_re.findall(r"🔴[📰📄📝🔍🏦💬]", t))
            neut = len(_re.findall(r"⚪[📰📄📝🔍🏦💬]", t))
            total = bull + bear + neut
            if total > 0:
                out["news_score"] = round((bull - bear) / total, 3)
                out["news_headlines"] = {"bullish": bull, "bearish": bear, "neutral": neut}
        except Exception:
            pass

    # analyst_intelligence — positional ticker + --json
    ai_path = _first_existing(_ANALYST_INT_PATH_CANDIDATES)
    if ai_path:
        try:
            import subprocess, re as _re
            cwd = os.path.dirname(ai_path)
            r = subprocess.run([sys.executable, ai_path, symbol, "--json"],
                               capture_output=True, text=True, timeout=45, cwd=cwd)
            t = (r.stdout or "")[:4000]
            out["analyst_raw"] = t[:300]
            # Try JSON parse first
            json_obj = None
            jm = _re.search(r"\{.*\}", t, _re.DOTALL)
            if jm:
                try: json_obj = json.loads(jm.group())
                except Exception: pass
            if json_obj:
                for k in ("consensus_score", "analyst_score", "score",
                         "buy_count", "sell_count", "hold_count",
                         "mean_target", "current_price", "upside_pct",
                         "strong_buy", "strong_sell", "buy", "sell", "hold"):
                    if k in json_obj:
                        out[f"analyst_{k}"] = json_obj[k]
                # Derive analyst_score if not directly given
                for sk in ("consensus_score", "analyst_score", "score"):
                    if sk in json_obj:
                        try:
                            v = float(json_obj[sk])
                            if -1.5 <= v <= 1.5:
                                out["analyst_score"] = round(v, 3)
                            elif -100 <= v <= 100:
                                out["analyst_score"] = round(v / 100.0, 3)
                            break
                        except (ValueError, TypeError):
                            pass
                if out["analyst_score"] is None:
                    # Count-based fallback: (buys - sells) / total
                    buys = sum(int(json_obj.get(k, 0) or 0)
                              for k in ("strong_buy", "buy", "buy_count"))
                    sells = sum(int(json_obj.get(k, 0) or 0)
                               for k in ("strong_sell", "sell", "sell_count"))
                    holds = sum(int(json_obj.get(k, 0) or 0)
                               for k in ("hold", "hold_count"))
                    tot = buys + sells + holds
                    if tot > 0:
                        out["analyst_score"] = round((buys - sells) / tot, 3)
            for pat in [r"analyst[_ ]score[:\s]+([+-]?[\d.]+)",
                        r"consensus[_ ]score[:\s]+([+-]?[\d.]+)"]:
                m = _re.search(pat, t, _re.IGNORECASE)
                if m:
                    try:
                        v = float(m.group(1))
                        if -1.5 <= v <= 1.5:
                            out["analyst_score"] = round(v, 3)
                            break
                        if -100 <= v <= 100:
                            out["analyst_score"] = round(v / 100.0, 3)
                            break
                    except ValueError:
                        pass
            # If no numeric score, infer from bullish/bearish word counts.
            if out["analyst_score"] is None and t:
                bulls = len(_re.findall(r"\b(BUY|OUTPERFORM|OVERWEIGHT|STRONG[_ ]BUY|UPGRADE)\b", t, _re.IGNORECASE))
                bears = len(_re.findall(r"\b(SELL|UNDERPERFORM|UNDERWEIGHT|STRONG[_ ]SELL|DOWNGRADE)\b", t, _re.IGNORECASE))
                if bulls + bears > 0:
                    out["analyst_score"] = round((bulls - bears) / (bulls + bears), 3)
        except Exception:
            pass

    parts = [v for v in (out["news_score"], out["analyst_score"]) if v is not None]
    if parts:
        out["combined_score"] = round(sum(parts) / len(parts), 3)
        _backend_cache_set(symbol, "sentiment", out)
        return out
    return None

def get_price_decomp_signals(symbol):
    """Bottoms-up price decomposition. football_field.py / epv_sotp.py / hg_dcf.py
    all use POSITIONAL ticker + --json. Tries each in order. 24h cache."""
    cached, refetch = _backend_cache_get(symbol, "price_decomp", 24 * 3600)
    if not refetch:
        return cached
    path = _first_existing(_PRICE_DECOMP_PATH_CANDIDATES)
    if not path:
        return None
    try:
        import subprocess, re as _re
        # Positional ticker + --json (works for football_field, epv_sotp, hg_dcf)
        r = subprocess.run([sys.executable, path, symbol, "--json"],
                           capture_output=True, text=True, timeout=90,
                           cwd=os.path.dirname(path))
        t = (r.stdout or "")[:6000]
        if not t:
            return None
        signals = {"backend": os.path.basename(path)}
        # Try JSON parse first
        json_obj = None
        m = _re.search(r"\{.*\}", t, _re.DOTALL)
        if m:
            try:
                json_obj = json.loads(m.group())
            except Exception:
                pass
        if json_obj:
            # football_field exposes: median_fair_value, current_price, upside_pct,
            # methods (8 valuation methods), verdict
            for k in ("median_fair_value", "fair_value", "intrinsic_value",
                     "current_price", "upside_pct", "gap_pct", "discount_pct",
                     "verdict", "recommendation", "implied_growth", "epv",
                     "sotp_value", "hg_dcf_value", "graham_number", "moat_rating"):
                if k in json_obj:
                    signals[k] = json_obj[k]
            # Normalize gap_pct from upside_pct/discount_pct
            if "gap_pct" not in signals:
                if "upside_pct" in signals:
                    signals["gap_pct"] = signals["upside_pct"]
                elif "discount_pct" in signals:
                    signals["gap_pct"] = -abs(signals["discount_pct"])
        else:
            # Text-mode fallback
            for key, pat in [
                ("fair_value", r"(?:median[_ ]fair[_ ]value|fair[_ ]value|intrinsic[_ ]value)[:\s$]+([\d.,]+)"),
                ("current_price", r"(?:current[_ ]price|spot)[:\s$]+([\d.,]+)"),
                ("gap_pct", r"(?:upside|gap|discount)[:\s]+([-+]?[\d.]+)%"),
                ("zscore", r"(?:z[_-]score|zscore)[:\s]+([-+]?[\d.]+)"),
                ("verdict", r"(?:verdict|recommendation|signal)[:\s]+([A-Z_]+)"),
            ]:
                m = _re.search(pat, t, _re.IGNORECASE)
                if m:
                    try:
                        if key in ("fair_value", "current_price"):
                            signals[key] = float(m.group(1).replace(",", ""))
                        elif key in ("gap_pct", "zscore"):
                            signals[key] = float(m.group(1))
                        else:
                            signals[key] = m.group(1)
                    except ValueError:
                        pass
        if any(k in signals for k in ("fair_value", "median_fair_value", "gap_pct", "zscore", "verdict")):
            _backend_cache_set(symbol, "price_decomp", signals)
            return signals
    except Exception:
        pass
    return None


def _run_simple_backend(symbol, candidates, ttl_sec, cache_key,
                        try_args=None, parse_keys=None):
    """v2.7 — generic backend runner for insider/EPS/options scripts.
    Tries multiple CLI arg shapes (--ticker, positional, --json). Returns
    a dict with whichever fields the parser regex matched, or None.

    try_args: list of [arg_template_lists] to attempt in order
    parse_keys: list of (key_name, regex_pattern) tuples
    """
    cached, refetch = _backend_cache_get(symbol, cache_key, ttl_sec)
    if not refetch:
        return cached
    path = _first_existing(candidates)
    if not path:
        return None
    try:
        import subprocess, re as _re
        cwd = os.path.dirname(path)
        out = ""
        for args in (try_args or [["--ticker", symbol, "--json"],
                                   [symbol, "--json"],
                                   ["--ticker", symbol],
                                   [symbol]]):
            r = subprocess.run([sys.executable, path] + args,
                               capture_output=True, text=True,
                               timeout=45, cwd=cwd)
            t = (r.stdout or "")
            if t.strip() and "usage:" not in t.lower()[:200] and \
               "error: unrecognized" not in t.lower():
                out = t[:6000]
                break
        if not out:
            return None
        signals = {}
        # JSON object first
        jm = _re.search(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", out, _re.DOTALL)
        if jm:
            try:
                obj = json.loads(jm.group())
                for k, v in obj.items():
                    if isinstance(v, (int, float, str, bool, list, dict)):
                        signals[k] = v
            except Exception:
                pass
        # Text regex fallback
        if parse_keys:
            for key, pat in parse_keys:
                m = _re.search(pat, out, _re.IGNORECASE)
                if m and key not in signals:
                    try:
                        v = m.group(1)
                        try:
                            signals[key] = float(v)
                        except (ValueError, TypeError):
                            signals[key] = v.strip()
                    except IndexError:
                        signals[key] = True
        if signals:
            _backend_cache_set(symbol, cache_key, signals)
            return signals
    except Exception:
        pass
    return None

def get_insider_signals(symbol):
    """insider_radar.py — SEC Form 4 insider buy/sell tracking. 24h cache."""
    return _run_simple_backend(
        symbol, _INSIDER_PATH_CANDIDATES, ttl_sec=86400, cache_key="insider",
        parse_keys=[
            ("buy_count", r"(?:insider[_ ]?buy|buy)[_ ]?count[:\s]+(\d+)"),
            ("sell_count", r"(?:insider[_ ]?sell|sell)[_ ]?count[:\s]+(\d+)"),
            ("net_buys", r"net[_ ]?buy[s]?[:\s]+([+-]?\d+)"),
            ("buy_value_usd", r"buy[_ ]?(?:value|usd)[:\s$]+([\d.,]+)"),
            ("sell_value_usd", r"sell[_ ]?(?:value|usd)[:\s$]+([\d.,]+)"),
            ("verdict", r"(?:insider[_ ]?signal|verdict)[:\s]+([A-Z_]+)"),
        ])

def get_eps_revision_signals(symbol):
    """earnings_revision_tracker.py — analyst EPS estimate revisions. 12h cache."""
    return _run_simple_backend(
        symbol, _EPS_REV_PATH_CANDIDATES, ttl_sec=12 * 3600, cache_key="eps_rev",
        parse_keys=[
            ("revision_direction", r"(?:revision[_ ]?direction|trend)[:\s]+(UP|DOWN|FLAT|MIXED)"),
            ("revision_magnitude", r"(?:revision[_ ]?magnitude|change)[:\s]+([+-]?[\d.]+)%?"),
            ("up_count", r"up[_ ]?(?:count|revisions)[:\s]+(\d+)"),
            ("down_count", r"down[_ ]?(?:count|revisions)[:\s]+(\d+)"),
            ("revision_score", r"(?:revision|momentum)[_ ]?score[:\s]+([+-]?\d+)"),
            ("eps_surprise_last", r"(?:eps[_ ]?surprise|last[_ ]?surprise)[:\s]+([+-]?[\d.]+)%?"),
        ])

def get_options_flow_signals(symbol):
    """options_flow.py — unusual options flow / put-call analysis. 6h cache."""
    return _run_simple_backend(
        symbol, _OPTIONS_FLOW_PATH_CANDIDATES, ttl_sec=6 * 3600, cache_key="opts_flow",
        parse_keys=[
            ("put_call_ratio", r"(?:put[_ ]?call|p/c)[_ ]?ratio[:\s]+([\d.]+)"),
            ("unusual_volume", r"unusual[_ ]?(?:volume|activity)[:\s]+(YES|NO|TRUE|FALSE)"),
            ("flow_direction", r"flow[_ ]?(?:direction|tilt)[:\s]+(BULLISH|BEARISH|NEUTRAL)"),
            ("call_volume", r"call[_ ]?volume[:\s]+([\d.,]+)"),
            ("put_volume", r"put[_ ]?volume[:\s]+([\d.,]+)"),
            ("iv_rank", r"iv[_ ]?rank[:\s]+([\d.]+)"),
            ("verdict", r"(?:options[_ ]?signal|verdict)[:\s]+([A-Z_]+)"),
        ])

def get_congress_signals(symbol):
    """congressional_signal.py — Form X disclosures of trades by members of
    Congress (Pelosi tracker, etc). 24h cache."""
    return _run_simple_backend(
        symbol, _CONGRESS_PATH_CANDIDATES, ttl_sec=86400, cache_key="congress",
        parse_keys=[
            ("buy_count", r"(?:congress[_ ]?)?buy[_ ]?count[:\s]+(\d+)"),
            ("sell_count", r"(?:congress[_ ]?)?sell[_ ]?count[:\s]+(\d+)"),
            ("net_direction", r"net[_ ]?(?:direction|tilt)[:\s]+(BUY|SELL|NEUTRAL)"),
            ("notable_trader", r"(?:notable|top)[_ ]?trader[:\s]+(\S+)"),
            ("trade_value_usd", r"(?:total[_ ]?)?value[_ ]?usd[:\s$]+([\d.,]+)"),
            ("last_trade_days", r"(?:last[_ ]?trade|days[_ ]?since)[:\s]+(\d+)"),
            ("verdict", r"(?:congress[_ ]?signal|verdict)[:\s]+([A-Z_]+)"),
        ])

# v2.8.1 — portfolio-scan backends: run ONCE per femisagent invocation,
# parse all tickers from output, lookup per-ticker.
_PORTFOLIO_SCAN_CACHE = {}

def _run_portfolio_scan(scan_key, candidates, args, parser_fn):
    """Run a portfolio-wide scan script once per femisagent process.
    `parser_fn(stdout)` returns dict[ticker -> signals].
    Cached in _PORTFOLIO_SCAN_CACHE for the lifetime of this process."""
    if scan_key in _PORTFOLIO_SCAN_CACHE:
        return _PORTFOLIO_SCAN_CACHE[scan_key]
    path = _first_existing(candidates)
    if not path:
        _PORTFOLIO_SCAN_CACHE[scan_key] = {}
        return {}
    try:
        import subprocess
        r = subprocess.run([sys.executable, path] + (args or []),
                           capture_output=True, text=True, timeout=180,
                           cwd=os.path.dirname(path))
        per_ticker = parser_fn(r.stdout or "") or {}
        _PORTFOLIO_SCAN_CACHE[scan_key] = per_ticker
        return per_ticker
    except Exception:
        _PORTFOLIO_SCAN_CACHE[scan_key] = {}
        return {}

def _parse_short_interest_output(text):
    """Format observed (v2.8.1):
      TICKER   SHORT%   vs.AVG    D2C  SHARES_S    MoM%    SQ  RISK
      CRWV     17.1%   +2.1pp    1.8     51.4M   -20.4%  100  🟠 HIGH
    """
    import re
    out = {}
    for line in text.splitlines():
        m = re.match(
            r"^\s*([A-Z]{1,5})\s+([\d.]+)%\s+([+-]?[\d.]+)pp\s+"
            r"([\d.]+)\s+([\d.]+)M?\s+([+-]?[\d.]+)%\s+(\d+)\s+\S+\s+(\w+)",
            line)
        if m:
            sym, si, vsavg, dtc, shares, mom, sq, risk = m.groups()
            out[sym] = {
                "short_pct_float": float(si),
                "vs_avg_pp": float(vsavg),
                "days_to_cover": float(dtc),
                "shares_short_M": float(shares),
                "mom_change_pct": float(mom),
                "squeeze_score": int(sq),
                "risk_label": risk,
            }
    return out

def _parse_volume_anomaly_output(text):
    """Format observed (v2.8.1):
      [vol] ⚡ GDX: 🔴 ALERT vol_z=3.07 price_z=-0.42
      [vol] ⚡ LRCX: 🔴 ALERT vol_z=3.59 price_z=-1.25
      [vol] ⚡ QQQM: 🟡 WATCH vol_z=2.29 price_z=-1.08
    """
    import re
    out = {}
    for line in text.splitlines():
        m = re.search(
            r"\[vol\]\s+\S+\s+([A-Z]{1,5}):\s+\S+\s+(ALERT|WATCH)\s+"
            r"vol_z=([+-]?[\d.]+)\s+price_z=([+-]?[\d.]+)",
            line)
        if m:
            sym, level, vz, pz = m.groups()
            out[sym] = {
                "alert_level": level,
                "volume_zscore": float(vz),
                "price_zscore": float(pz),
                "anomaly_type": "SURGE" if float(vz) > 3 else "WATCH",
            }
    return out

def get_short_interest_signals(symbol):
    """v2.8.1: short_interest_monitor.py is portfolio-wide (no --ticker
    flag). Runs once with --no-ibkr; parses all tickers from output table."""
    return _run_portfolio_scan(
        "short_interest", _SHORT_INT_PATH_CANDIDATES, ["--no-ibkr"],
        _parse_short_interest_output
    ).get(symbol)

def get_volume_anomaly_signals(symbol):
    """v2.8.1: volume_anomaly_monitor.py is portfolio-wide (no --ticker
    flag). Runs once with --dry-run --force; parses all alert lines."""
    return _run_portfolio_scan(
        "vol_anomaly", _VOL_ANOMALY_PATH_CANDIDATES, ["--dry-run", "--force"],
        _parse_volume_anomaly_output
    ).get(symbol)

def get_adaptive_predictor_signals(symbol):
    """adaptive_predictor.py — ML-based return predictor. 6h cache.
    CLI unknown; _run_simple_backend tries --ticker SYM --json first,
    then positional, then bare invocations."""
    return _run_simple_backend(
        symbol, _ADAPTIVE_PRED_PATH_CANDIDATES, ttl_sec=6 * 3600,
        cache_key="adaptive_pred",
        parse_keys=[
            ("predicted_return_pct", r"(?:predicted|forecast)[_ ]?return[:\s]+([+-]?[\d.]+)%?"),
            ("confidence", r"confidence[:\s]+([\d.]+)%?"),
            ("direction", r"(?:direction|signal)[:\s]+(UP|DOWN|FLAT|BUY|SELL|HOLD)"),
            ("model", r"model[:\s]+(\S+)"),
            ("horizon_days", r"horizon[_ ]?days?[:\s]+(\d+)"),
            ("verdict", r"(?:predictor[_ ]?verdict|verdict)[:\s]+([A-Z_]+)"),
        ])


# Set of flag names known to be bullish-tilted across past calibrations.
# Used by MULTI_CONFLUENCE_* meta-flags to count concurring bullish signals.
_BULLISH_FLAGS = frozenset({
    "GS_ACCUM", "GS_MILD_ACCUM", "SQUEEZE_BULLISH",
    "20D_BREAKOUT", "MOMENTUM_SURGE", "VPIN_TOXIC",
    "MOMENTUM_CONTINUATION", "HRT_STRONG", "HRT_STRONG_v4",
    "PARABOLIC_BLOCK", "PARABOLIC_RECOVERY", "PARABOLIC_CRISIS",
    "PARABOLIC_TRENDED", "HRT_REVERSAL_RISK", "GS_DISTRIB",
    "HRT_WEAK", "SQUEEZE_RESOLVING_BULL", "SQUEEZE_RESOLVING_DOWN",
    "OBV_THRUST", "VPIN_THRUST",
    # v1.8.2.1 kept:
    "RSI_REVERSAL", "RSI_OVERBOUGHT_CONT", "ATR_BREAKOUT",
})

def compute_flags(bars, spy_ret_20d=None, spy_ret_60d=None, spy_60d_dd_pct=None):
    """Given list of OHLCV bars (oldest first), return FEMISAPIEN flags.

    v1.7: optional regime context params:
        spy_ret_20d     — SPY 20d return as decimal (HRT_STRONG_v4 gate)
        spy_ret_60d     — SPY 60d return as decimal (regime classifier)
        spy_60d_dd_pct  — SPY drawdown from 60d high as decimal, negative
                          (PARABOLIC_RECOVERY gate)
    None values silently disable the dependent flag rules.
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

    # v1.7: also need yesterday's bb_squeeze for SQUEEZE_RESOLVING_*.
    # bb_width_hist[-1] is yesterday's bb_width (window closes[-21:-1]).
    if len(bb_width_hist) >= 21:
        yesterday_avg_width = sum(bb_width_hist[-21:-1]) / 20
        bb_squeeze_yesterday = bb_width_hist[-1] < (yesterday_avg_width * 0.75)
    else:
        bb_squeeze_yesterday = False

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

    # ── Original flag rules (kept) ───────────────────────────────────────

    if vol_ratio >= 2.5 and ret_1d > 0.01 and uptrend:
        flags.append("GS_ACCUM")
    elif vol_ratio >= 1.5 and ret_1d > 0.005 and uptrend:
        flags.append("GS_MILD_ACCUM")

    if breakout_20d and vol_ratio > 1.2:
        flags.append("20D_BREAKOUT")

    if ret_5d > 0.08 and vol_ratio > 1.4 and uptrend:
        flags.append("MOMENTUM_SURGE")
    elif ret_10d > 0.05 and ret_5d > 0.02 and uptrend:
        flags.append("MOMENTUM_CONTINUATION")

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

    # ── v1.6 kept (proven edge) ──────────────────────────────────────────

    # SQUEEZE_BULLISH: +18.04% bull excess on n=94. Mediocre WR (54%) was
    # incorrectly Wilson-demoted; v1.4 backtest now requires excess<0 too.
    if bb_squeeze and -0.05 < ret_5d < 0.05 and macd_hist > 0:
        flags.append("SQUEEZE_BULLISH")

    # OBV_BEAR_DIVERGENCE: regime-conditional (-3.3% bull, +4.2% bear).
    # Useful both ways. Live scorer treats as AVOID when bull; signal
    # consumer reverses interpretation when SPY 60d return < 0.
    if obv_normalized < 0.0 and ret_20d > 0.05:
        flags.append("OBV_BEAR_DIVERGENCE")

    # ── v1.7 reworks of the 6 failed/dead flag families ──────────────────

    # HRT_STRONG_v4: like v3 (both absolute + SPY-relative gates) but
    # WITHOUT the MOMENTUM_CONTINUATION-not-in-flags exclusion. v3 had 0
    # fires because the exclusion clause killed every candidate. v4 lets
    # it co-fire as a conviction overlay on momentum signals.
    if spy_ret_20d is not None:
        if uptrend and ret_20d > 0.10 and ret_20d > spy_ret_20d + 0.10 and ret_5d > 0:
            flags.append("HRT_STRONG_v4")

    # VPIN_THRUST: replaces VPIN_ELEVATED. Stricter conditions to capture
    # actual buying thrust (not just lagging trend echo): explosive
    # up-day with volume + trend confirmation.
    if vpin > 0.65 and ret_1d > 0.01 and uptrend and vol_ratio > 1.5:
        flags.append("VPIN_THRUST")

    # SQUEEZE_RESOLVING_BULL: replaces SQUEEZE_PRE_BREAKOUT. Fires on the
    # TRANSITION: squeeze yesterday → no squeeze today + up-day thrust +
    # volume. Catches the breakout itself, not pre or post.
    if bb_squeeze_yesterday and not bb_squeeze and ret_1d > 0.01 and vol_ratio > 1.5:
        flags.append("SQUEEZE_RESOLVING_BULL")

    # SQUEEZE_RESOLVING_DOWN: same transition with a down-day. Originally
    # named "_BEAR" assuming this would be a bearish signal — backtest
    # showed it's actually BULLISH (+7.94% bull, +2.99% bear excess).
    # The squeeze-release energy is bullish regardless of immediate direction.
    if bb_squeeze_yesterday and not bb_squeeze and ret_1d < -0.01 and vol_ratio > 1.5:
        flags.append("SQUEEZE_RESOLVING_DOWN")

    # OBV_THRUST: replaces OBV_BULL_DIVERGENCE. The original "OBV up while
    # price flat" hypothesis (accumulation) failed (-4.43% excess). v2:
    # require CONCURRENT momentum in OBV + price. Confirmation, not
    # divergence.
    if obv_normalized > 0.30 and ret_20d > 0.05 and ret_5d > 0:
        flags.append("OBV_THRUST")

    # PARABOLIC_RECOVERY: refines PARABOLIC_BLOCK by gating on regime.
    # v1.7 first attempt used "SPY 60d-drawdown < -8%" — that fired 0 times
    # because PARABOLIC (stock +40% in 20d) and "SPY currently 8%+ below 60d
    # high" are time-opposites: when SPY is deeply down, stocks are too;
    # when stocks go parabolic, SPY has usually already recovered enough
    # that 60d-drawdown isn't < -8% anymore.
    #
    # v1.7.1: redefine to "SPY ret_60d > 0" — i.e., the parabolic move
    # happens during a recovering market, not a still-falling one.
    # Filters 2008-style dead-cat-bounce fires (SPY still falling) from
    # 2009 / 2023+ real-recovery fires (SPY net positive over 60d).
    if parabolic and spy_ret_60d is not None and spy_ret_60d > 0:
        flags.append("PARABOLIC_RECOVERY")

    # PARABOLIC_CRISIS: parabolic during SPY-down regime. Original raw
    # backtest showed +13.83% bull excess, but per-year breakdown
    # revealed bimodal distribution: 2022 fires (true crisis) lost
    # -12.68% avg, 2025 fires (brief SPY pullback + thematic explosions)
    # made +53.68%. To preserve only the validated-strength fires and
    # filter the 2022 dead-cat losers, v1.7.4 requires concurrent
    # "real strength" confirmation: either OBV_THRUST (volume thrust)
    # OR HRT_STRONG_v4 (sustained relative-strength uptrend including
    # ma50). Those proxies for "real catalyst" should exclude failed
    # momentum pops.
    if parabolic and spy_ret_60d is not None and spy_ret_60d <= 0:
        if "OBV_THRUST" in flags or "HRT_STRONG_v4" in flags:
            flags.append("PARABOLIC_CRISIS")

    # ── v1.8.2 — new factors targeting win-rate lift ───────────────────────
    # RSI, ATR computed once per call; cheap (linear over the window).
    rsi14 = _rsi(closes, 14)
    rsi_prev = _rsi(closes[:-1], 14) if len(closes) >= 16 else 50.0
    atr14 = _atr(highs, lows, closes, 14)

    # RSI_REVERSAL: classic oversold-bounce pattern. RSI crosses up
    # through 30 from below + today is a green bar.
    # v1.8.2.1: gated on bull regime (spy_ret_60d > 0). Oversold bounces
    # work in trending bull markets; in bear regimes they're falling
    # knives. Bull: 69.0% WR / +5pp excess. Bear ungated: 49.5% WR /
    # -2.16% excess. Bull-gating preserves the win.
    if spy_ret_60d is not None and spy_ret_60d > 0:
        if rsi_prev < 30 and rsi14 >= 30 and ret_1d > 0:
            flags.append("RSI_REVERSAL")

    # RSI_OVERBOUGHT_CONT: RSI > 75 + recent run-up. Predicted to be
    # a fade warning. Backtest showed it's actually BULLISH (+5.53%
    # excess in bull). Momentum continues in this universe — overbought
    # is a continuation signal, not a reversal warning. Renamed to
    # reflect "continuation," not "warning."
    if rsi14 > 75 and ret_5d > 0.05:
        flags.append("RSI_OVERBOUGHT_CONT")

    # ATR_BREAKOUT: 20d-high breakout that's at least 1.5 ATRs above
    # prior high. Filters out marginal breakouts that fade. Volatility-
    # adjusted so it's comparable across noisy/quiet names.
    if breakout_20d and atr14 > 0 and (c - high_20d) > 1.5 * atr14:
        flags.append("ATR_BREAKOUT")

    # PARABOLIC_TRENDED: refined PARABOLIC family. Bull: 51.4% WR
    # (didn't lift WR over PARABOLIC_BLOCK's 52.2%, trend filter
    # doesn't fix the fat tail). Bear: 78.0% WR / +16.84% excess —
    # strong bear-regime signal. Kept for bear coverage even though
    # bull WR is unchanged; the avg_ret is still excellent.
    if parabolic and c > ma50 and ma20 > ma50:
        flags.append("PARABOLIC_TRENDED")

    # MULTI_CONFLUENCE_3: meta-flag — 3+ concurrent bullish flags. Modest
    # positive excess both regimes (+1.1pp bull, +2.6pp bear excess_wr).
    # Kept as a low-noise confluence indicator. (MULTI_CONFLUENCE_5 was
    # dropped: -3.6pp bull WR — too-crowded setups mean-revert.)
    bullish_count = sum(1 for f in flags if f in _BULLISH_FLAGS)
    if bullish_count >= 3:
        flags.append("MULTI_CONFLUENCE_3")

    # v2.4 — Ceyhun Overbought/Oversold (Pine v4 port). Triple-EMA-smoothed
    # z-score; fires BUY on up-crossover, SELL on up-crossunder. n=5 per the
    # original. No FLAG_STATS entry yet → EV=0 (informational only); the next
    # backtest cycle calibrates and the Wilson-CI demote rule promotes/demotes
    # based on real edge.
    obob = ceyhun_obob_signal(bars, n=5)
    if obob == "BUY":
        flags.append("CEYHUN_OBOB_BUY")
    elif obob == "SELL":
        flags.append("CEYHUN_OBOB_SELL")

    # v2.5 — Trend Trader-Remastered (TTR) by aybarsm, Pine v6.
    # PSAR-based entry crossover with the indicator's unusual max=0.025
    # 'lagging' configuration. Entry-only port; TP/RE features require
    # stateful position tracking out of scope here. EV=0 informational
    # until backtest calibrates, same as Ceyhun_OBOB.
    ttr = ttr_signal(bars)
    if ttr == "BUY":
        flags.append("TTR_BUY")
    elif ttr == "SELL":
        flags.append("TTR_SELL")
    else:
        # v2.5.1: TTR_NEAR_FLIP — PSAR within 1% of price = flip likely on
        # next bar. Way more actionable than waiting for the actual crossover
        # on a low-frequency signal. Direction inferred from PSAR position
        # relative to price.
        # v2.5.2: bumped to 2% — empirically NVDA at 1.39% gap to PSAR
        # was the kind of imminent setup worth surfacing.
        ttr_near = ttr_near_flip(bars, threshold_pct=2.0)
        if ttr_near == "BEARISH_NEAR":
            flags.append("TTR_NEAR_SELL")
        elif ttr_near == "BULLISH_NEAR":
            flags.append("TTR_NEAR_BUY")

    # v2.6 — Libertus RSI Divergence (Pine v4 port). Classic momentum-vs-price
    # divergence on 14-period RSI / 90-bar lookback. EV=0 informational until
    # backtest cycle calibrates.
    rsi_div = libertus_rsi_div_signal(bars, length=14, xbars=90)
    if rsi_div == "BEAR_DIV":
        flags.append("RSI_DIV_BEAR")
    elif rsi_div == "BULL_DIV":
        flags.append("RSI_DIV_BULL")

    # v1.8.2.1 dropped vs v1.8.2:
    #   TREND_QUALITY    — fired on 47% of all bars, pure noise
    #   MULTI_CONFLUENCE_5 — -3.6pp bull WR vs baseline
    #   RSI_OVERBOUGHT  → renamed RSI_OVERBOUGHT_CONT (semantic fix)

    def _edge(f):
        s = FLAG_STATS.get(f, {})
        return s["excess_ret"] if "excess_ret" in s else s.get("avg_ret", 0)
    pos = [f for f in flags if _edge(f) > 0]
    neg = [f for f in flags if _edge(f) <= 0]
    # v1.7.2: sort by edge magnitude (best pos first, worst neg first)
    # so primary = strongest positive signal, secondary = strongest warning.
    pos.sort(key=lambda f: (-_edge(f), FLAG_STATS.get(f, {}).get("priority", 999)))
    neg.sort(key=lambda f: (_edge(f), FLAG_STATS.get(f, {}).get("priority", 999)))

    # v1.7.2 fix: previously returned only [pos[0], neg[0]] — culled all
    # other fires. New flags (HRT_STRONG_v4, PARABOLIC_RECOVERY, etc.)
    # had no FLAG_STATS entry so _edge=0, sorted last, never returned,
    # never tracked by backtest. Now return ALL fired flags with primary
    # (top positive) + secondary (top negative) at front, rest appended.
    result = []
    if pos: result.append(pos[0])
    if neg: result.append(neg[0])
    result.extend(pos[1:])
    result.extend(neg[1:])
    return result if result else ["NEUTRAL"]

# ── IBKR data fetch ─────────────────────────────────────────────────────────
async def fetch_bars(ib, ticker, exchange="SMART", currency="USD"):
    if USE_BRIDGE and BRIDGE_TOKEN:
        raw = fetch_bars_via_bridge(ticker)
        if raw:
            return [make_bar_obj(d) for d in raw]
        # v2.1: bridge returned empty — yfinance fallback
        yf_bars = _fetch_bars_via_yfinance(ticker)
        if yf_bars:
            return yf_bars
        return []
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
        # v1.8.2: route portfolio fetch through bridge when bridge mode is
        # active. Direct ib.reqPositionsAsync() requires a connected IB
        # client; we never connect in bridge mode.
        if USE_BRIDGE and BRIDGE_TOKEN:
            print("Fetching portfolio via bridge...")
            positions = get_positions_via_bridge()
        else:
            positions = await get_portfolio_tickers(ib)
        for sym, exch, cur, qty, cost in positions:
            portfolio_data[sym] = {"qty": qty, "avgCost": cost}
        if not tickers:
            tickers = [p[0] for p in positions]
        else:
            # v1.8.2: union — when both --portfolio and --tickers are given,
            # scan the watchlist AND add any held tickers not already in it.
            # Portfolio_data annotations still apply for held names.
            existing = set(tickers)
            for sym, *_ in positions:
                if sym not in existing:
                    tickers.append(sym)
                    existing.add(sym)
            print(f"Watchlist union with portfolio: {len(tickers)} total tickers "
                  f"({len(existing) - len(positions)} watchlist-only, "
                  f"{len(positions)} held)")

    # v1.7: pre-fetch SPY for regime context (20d, 60d, 60d-drawdown).
    spy_ret_20d = None
    spy_ret_60d = None
    spy_60d_dd_pct = None
    spy_bars = await fetch_bars(ib, "SPY")
    if len(spy_bars) >= 61:
        c_now = spy_bars[-1].close
        spy_ret_20d = (c_now - spy_bars[-21].close) / spy_bars[-21].close
        spy_ret_60d = (c_now - spy_bars[-61].close) / spy_bars[-61].close
        high_60d = max(b.close for b in spy_bars[-60:])
        spy_60d_dd_pct = (c_now - high_60d) / high_60d
        print(f"SPY regime: 20d={spy_ret_20d*100:+.2f}% 60d={spy_ret_60d*100:+.2f}% "
              f"60d-dd={spy_60d_dd_pct*100:+.2f}% (v1.7 regime gates active)")
    elif len(spy_bars) >= 21:
        spy_ret_20d = (spy_bars[-1].close - spy_bars[-21].close) / spy_bars[-21].close
        print(f"SPY partial (20d only): {spy_ret_20d*100:+.2f}% — PARABOLIC_RECOVERY will not fire")
    else:
        print("SPY benchmark unavailable; v1.7 regime-gated flags will not fire")

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

        flags = compute_flags(bars, spy_ret_20d=spy_ret_20d,
                              spy_ret_60d=spy_ret_60d,
                              spy_60d_dd_pct=spy_60d_dd_pct)
        primary = flags[0]
        secondary = flags[1] if len(flags) > 1 else None

        c = bars[-1].close
        c20 = bars[-20].close
        ret_20d = (c - c20) / c20
        avg_vol = sum(b.volume for b in bars[-20:]) / 20
        vol_ratio = bars[-1].volume / avg_vol if avg_vol > 0 else 1.0

        # v1.8.2: capture full flag list (not just primary/secondary) so
        # the report can show all fires per ticker. Other v1.7+ flags
        # (RSI_REVERSAL, MULTI_CONFLUENCE_3, RSI_OVERBOUGHT_CONT, ...)
        # were firing invisibly before because the print/JSON only
        # surfaced flags[0] and flags[1].
        all_flags = [f for f in flags
                     if f not in ("NEUTRAL", "INSUFFICIENT_DATA", "NO_DATA")]
        # v2.5.2 — capture current PSAR value + distance so the report
        # always shows TTR proximity to flip (not just on flag fires).
        try:
            _highs = [b.high for b in bars]
            _lows = [b.low for b in bars]
            _psar_series = _psar(_highs, _lows, 0.02, 0.02, 0.025)
            _psar_now = _psar_series[-1]
            if _psar_now is not None and c > 0:
                _ttr_dist_pct = (c - _psar_now) / c * 100.0
                _ttr_position = "below" if _psar_now < c else "above"
            else:
                _ttr_dist_pct = None
                _ttr_position = None
        except Exception:
            _ttr_dist_pct = None
            _ttr_position = None

        row = {
            "symbol":     sym,
            "price":      round(c, 2),
            "ret_20d_pct": round(ret_20d * 100, 1),
            "vol_ratio":  round(vol_ratio, 2),
            "flag":       primary,
            "flag2":      secondary,
            "all_flags":  all_flags,
            "ttr_psar":   round(_psar_now, 2) if _psar_now is not None else None,
            "ttr_dist_pct": round(_ttr_dist_pct, 2) if _ttr_dist_pct is not None else None,
            "ttr_position": _ttr_position,
            "ev_score":   ev_score(primary),
            "verdict":    signal_verdict(primary),
            "bars":       len(bars),
        }

        # v1.9 gate 1 — earnings calendar: demote if within 5 days
        dte = get_earnings_dte(sym)
        row["earnings_dte"] = dte
        if dte is not None and 0 <= dte <= 5:
            row["all_flags"].append("EARNINGS_BLOCK")
            row["ev_score"] = min(row["ev_score"], 0.5)
            row["verdict"] = "🟡 WATCH"
            row["gate"] = f"EARNINGS_IN_{dte}D"

        # v1.9 gate 2 — Beneish M-Score: demote if accruals/manipulation risk
        m = get_beneish_m(sym)
        row["beneish_m"] = m
        if m is not None and m > -1.78:
            row["all_flags"].append("BENEISH_RISK")
            if row["ev_score"] >= 2:
                row["ev_score"] = min(row["ev_score"], 0.5)
                row["verdict"] = "🟡 WATCH"
            row["gate"] = row.get("gate") or f"BENEISH_M={m}"

        # v1.9 gate 3 — PARABOLIC cap: fat-tail flag, never EXECUTE-tier
        if primary == "PARABOLIC_BLOCK" and row["ev_score"] >= 4:
            row["ev_score"] = min(row["ev_score"], 3.5)
            row["verdict"] = "🟢 BUY"
            row["gate"] = row.get("gate") or "PARABOLIC_CAP"

        # v1.10 gate 4 — 13F retail-heavy: demote EXECUTE when inst_pct < 10
        thirteen_f = get_13f_context(sym)
        row["thirteen_f"] = thirteen_f
        if thirteen_f and thirteen_f.get("inst_pct") is not None:
            inst_pct = thirteen_f["inst_pct"]
            if inst_pct < 10 and row["ev_score"] >= 4:
                row["ev_score"] = min(row["ev_score"], 3.5)
                row["verdict"] = "🟢 BUY"
                row["gate"] = row.get("gate") or f"RETAIL_HEAVY_INST={inst_pct}%"

        # v2.0 — femisapien cross-engine gates (quant signal layering)
        femisapien = get_femisapien_signals(sym)
        row["femisapien"] = femisapien
        if femisapien:
            # Gate 5: VPIN_TOXIC — informed-flow toxicity
            if femisapien.get("vpin_signal") == "TOXIC":
                row["all_flags"].append("VPIN_TOXIC_FLOW")
                if row["ev_score"] >= 4:
                    row["ev_score"] = min(row["ev_score"], 3.5)
                    row["verdict"] = "🟢 BUY"
                    row["gate"] = row.get("gate") or "VPIN_TOXIC"

            # Gate 6: femisapien momentum exhaustion (REDUCE_HALF, TRIM_50)
            exh = femisapien.get("momentum_exhaustion_label")
            if exh in ("REDUCE_HALF", "TRIM_50", "TRIM_HALF"):
                row["all_flags"].append(f"FA_EXH_{exh}")
                if row["ev_score"] >= 4:
                    row["ev_score"] = min(row["ev_score"], 3.5)
                    row["verdict"] = "🟢 BUY"
                    row["gate"] = row.get("gate") or f"FA_{exh}"

            # Gate 7: cross-asset dislocation (false = sector vs SPY breakdown)
            if femisapien.get("cross_asset_gate") is False:
                row["all_flags"].append("CROSS_ASSET_RISK")
                if row["ev_score"] >= 4:
                    row["ev_score"] = min(row["ev_score"], 3.5)
                    row["verdict"] = "🟢 BUY"
                    row["gate"] = row.get("gate") or "CROSS_ASSET"

            # Gate 8: LPPLS bubble + high crash probability
            crash_p = femisapien.get("lppls_crash_prob") or 0
            if femisapien.get("lppls_bubble") == "BUBBLE" and crash_p > 0.5:
                row["all_flags"].append(f"LPPLS_BUBBLE_{int(crash_p*100)}")
                if row["ev_score"] >= 4:
                    row["ev_score"] = min(row["ev_score"], 3.5)
                    row["verdict"] = "🟢 BUY"
                    row["gate"] = row.get("gate") or "LPPLS_BUBBLE"

            # Gate 9: HGDCF priced-for-perfection (caution flag, no auto-demote)
            if femisapien.get("hgdcf_signal") == "PRICED_FOR_PERFECTION":
                row["all_flags"].append("HGDCF_PFP")

            # Confirmation: femisapien strong conviction overlap
            conv = femisapien.get("conviction_pct") or 0
            if conv >= 50:
                row["all_flags"].append(f"FA_CONVICTION_{int(conv)}")

        # v2.3 — TA Fusion cross-engine gates
        taf = get_ta_fusion_signals(sym)
        row["ta_fusion"] = taf
        if taf:
            # Overbought combo: both Williams%R and CCI flag SELL/OVERBOUGHT
            w = (taf.get("williams_signal") or "").upper()
            c = (taf.get("cci_signal") or "").upper()
            if w in ("SELL",) and c in ("OVERBOUGHT",):
                row["all_flags"].append("TAF_OVERBOUGHT")
                if row["ev_score"] >= 4:
                    row["ev_score"] = min(row["ev_score"], 3.5)
                    row["verdict"] = "🟢 BUY"
                    row["gate"] = row.get("gate") or "TAF_OVERBOUGHT"
            # OBV bearish divergence — well-validated topping signal
            if (taf.get("obv_div") or "").upper() == "BEARISH":
                row["all_flags"].append("TAF_OBV_BEAR_DIV")
                if row["ev_score"] >= 4:
                    row["ev_score"] = min(row["ev_score"], 3.5)
                    row["verdict"] = "🟢 BUY"
                    row["gate"] = row.get("gate") or "TAF_OBV_BEAR_DIV"
            # HL cycle peak (>30 bars)
            if (taf.get("hl_cycle_phase") or "").upper() == "PEAK":
                row["all_flags"].append("TAF_PEAK_CYCLE")
                if row["ev_score"] >= 4:
                    row["ev_score"] = min(row["ev_score"], 3.5)
                    row["verdict"] = "🟢 BUY"
                    row["gate"] = row.get("gate") or "TAF_PEAK"
            # Confirmation flags — no demote
            if (taf.get("hl_cycle_phase") or "").upper() == "TROUGH":
                row["all_flags"].append("TAF_TROUGH_CYCLE")
            if (taf.get("executable") or "").upper() == "YES" and (taf.get("conviction_pct") or 0) >= 50:
                row["all_flags"].append(f"TAF_EXEC_{int(taf['conviction_pct'])}")

        # v2.3 — sentiment gates (news_radar + analyst_intelligence combined)
        sent = get_sentiment_signals(sym)
        row["sentiment"] = sent
        if sent and sent.get("combined_score") is not None:
            score = sent["combined_score"]
            if score < -0.5 and row["ev_score"] >= 4:
                row["all_flags"].append(f"SENT_NEG_{score:.2f}")
                row["ev_score"] = min(row["ev_score"], 3.5)
                row["verdict"] = "🟢 BUY"
                row["gate"] = row.get("gate") or f"SENT_NEG={score:.2f}"
            elif score > 0.5:
                row["all_flags"].append(f"SENT_CONFIRM_{score:+.2f}")

        # v2.3 — price decomposition (bottoms-up valuation)
        pd_sig = get_price_decomp_signals(sym)
        row["price_decomp"] = pd_sig
        if pd_sig:
            gap = pd_sig.get("gap_pct")
            if gap is not None:
                if gap < -20 and row["ev_score"] >= 4:
                    row["all_flags"].append(f"PD_OVERVALUED_{gap:.0f}")
                    row["ev_score"] = min(row["ev_score"], 3.5)
                    row["verdict"] = "🟢 BUY"
                    row["gate"] = row.get("gate") or f"PD_GAP={gap:.0f}%"
                elif gap > 20:
                    row["all_flags"].append(f"PD_UPSIDE_{gap:+.0f}")

        # v2.7 — insider radar (Form 4 buy/sell clustering)
        ins = get_insider_signals(sym)
        row["insider"] = ins
        if ins:
            buys = int(ins.get("buy_count", 0) or 0)
            sells = int(ins.get("sell_count", 0) or 0)
            if buys >= 3 and buys > sells:
                row["all_flags"].append(f"INSIDER_BUY_CLUSTER_{buys}")
            elif sells >= 3 and sells > buys * 2:
                row["all_flags"].append(f"INSIDER_SELL_HEAVY_{sells}")
                if row["ev_score"] >= 4:
                    row["ev_score"] = min(row["ev_score"], 3.5)
                    row["verdict"] = "🟢 BUY"
                    row["gate"] = row.get("gate") or f"INSIDER_SELL_{sells}"

        # v2.7 — EPS revisions (analyst estimate momentum)
        eps = get_eps_revision_signals(sym)
        row["eps_rev"] = eps
        if eps:
            direction = (eps.get("revision_direction") or "").upper()
            score = eps.get("revision_score")
            if direction == "DOWN" or (score is not None and float(score) <= -2):
                row["all_flags"].append("EPS_REV_DOWN")
                if row["ev_score"] >= 4:
                    row["ev_score"] = min(row["ev_score"], 3.5)
                    row["verdict"] = "🟢 BUY"
                    row["gate"] = row.get("gate") or "EPS_REV_DOWN"
            elif direction == "UP" and score is not None and float(score) >= 2:
                row["all_flags"].append(f"EPS_REV_UP_STRONG_{score}")

        # v2.7 — options flow (put/call + unusual volume)
        opts = get_options_flow_signals(sym)
        row["opts_flow"] = opts
        if opts:
            pcr = opts.get("put_call_ratio")
            flow = (opts.get("flow_direction") or "").upper()
            if pcr is not None:
                try:
                    pcr = float(pcr)
                    if pcr > 2.0 or flow == "BEARISH":
                        row["all_flags"].append(f"OPTIONS_PUT_HEAVY_{pcr:.2f}")
                        if row["ev_score"] >= 4:
                            row["ev_score"] = min(row["ev_score"], 3.5)
                            row["verdict"] = "🟢 BUY"
                            row["gate"] = row.get("gate") or f"OPTIONS_PUT_HEAVY={pcr:.2f}"
                    elif pcr < 0.4 or flow == "BULLISH":
                        row["all_flags"].append(f"OPTIONS_CALL_HEAVY_{pcr:.2f}")
                except (ValueError, TypeError):
                    pass

        # v2.8 — Congressional trades (Pelosi-style tracker)
        cong = get_congress_signals(sym)
        row["congress"] = cong
        if cong:
            cbuy = int(cong.get("buy_count", 0) or 0)
            csell = int(cong.get("sell_count", 0) or 0)
            cdir = (cong.get("net_direction") or "").upper()
            if cbuy >= 2 and (cdir == "BUY" or cbuy > csell):
                row["all_flags"].append(f"CONGRESS_BUY_{cbuy}")
            elif csell >= 2 and (cdir == "SELL" or csell > cbuy):
                row["all_flags"].append(f"CONGRESS_SELL_{csell}")
                if row["ev_score"] >= 4:
                    row["ev_score"] = min(row["ev_score"], 3.5)
                    row["verdict"] = "🟢 BUY"
                    row["gate"] = row.get("gate") or f"CONGRESS_SELL_{csell}"

        # v2.8 — Short interest (squeeze risk)
        si = get_short_interest_signals(sym)
        row["short_int"] = si
        if si:
            spct = si.get("short_pct_float")
            dtc = si.get("days_to_cover")
            try:
                spct = float(spct) if spct is not None else None
                dtc = float(dtc) if dtc is not None else None
                if spct is not None and dtc is not None:
                    if spct > 20 and dtc > 5:
                        row["all_flags"].append(f"SHORT_SQUEEZE_RISK_si{spct:.0f}_dtc{dtc:.1f}")
                    elif spct > 15:
                        row["all_flags"].append(f"SHORT_ELEVATED_{spct:.0f}")
            except (ValueError, TypeError):
                pass

        # v2.8 — Volume anomaly (z-score outliers vs 20-day avg)
        vola = get_volume_anomaly_signals(sym)
        row["vol_anomaly"] = vola
        if vola:
            z = vola.get("volume_zscore")
            atype = (vola.get("anomaly_type") or "").upper()
            try:
                z = float(z) if z is not None else None
                if z is not None:
                    if z > 3 or atype == "SURGE":
                        row["all_flags"].append(f"VOL_ANOMALY_SURGE_z{z:.1f}")
                    elif z < -2 or atype == "COLLAPSE":
                        row["all_flags"].append(f"VOL_ANOMALY_COLLAPSE_z{z:.1f}")
            except (ValueError, TypeError):
                pass

        # v2.8.1 — Adaptive predictor (ML-based return forecast)
        adp = get_adaptive_predictor_signals(sym)
        row["adaptive_pred"] = adp
        if adp:
            pred_ret = adp.get("predicted_return_pct")
            direction = (adp.get("direction") or "").upper()
            try:
                pred_ret = float(pred_ret) if pred_ret is not None else None
                if pred_ret is not None:
                    if pred_ret <= -5 or direction in ("DOWN", "SELL"):
                        row["all_flags"].append(f"ADP_PRED_DOWN_{pred_ret:+.1f}")
                        if row["ev_score"] >= 4:
                            row["ev_score"] = min(row["ev_score"], 3.5)
                            row["verdict"] = "🟢 BUY"
                            row["gate"] = row.get("gate") or f"ADP_PRED={pred_ret:+.1f}%"
                    elif pred_ret >= 5 or direction in ("UP", "BUY"):
                        row["all_flags"].append(f"ADP_PRED_UP_{pred_ret:+.1f}")
            except (ValueError, TypeError):
                pass

        if sym in portfolio_data:
            pos = portfolio_data[sym]
            row["qty"]      = pos["qty"]
            # v2.3.3: defensive float casts — bridge returns sometimes vary in type
            try:
                ac = float(pos["avgCost"])
                cf = float(c)
                row["avg_cost"] = round(ac, 2)
                row["unreal_pct"] = round((cf - ac) / ac * 100, 1) if ac else 0
            except (TypeError, ValueError, KeyError):
                row["avg_cost"] = pos.get("avgCost")
                row["unreal_pct"] = 0

        results.append(row)
        gate_note = f" [{row['gate']}]" if row.get("gate") else ""
        print(f"{primary} | EV={row['ev_score']} | {row['verdict']}{gate_note}")
        await asyncio.sleep(0.4)

    ib.disconnect()
    return results

def print_report(results):
    results.sort(key=lambda x: x["ev_score"], reverse=True)
    print("\n" + "="*80)
    print(f"FEMISAGENT v2.8.1 (19-engine + portfolio-scan + Adaptive_Predictor) — SIGNAL REPORT | {datetime.now().strftime('%Y-%m-%d %H:%M')}")
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
            # v1.8.2: surface every additional flag that fired for this
            # ticker (beyond primary/secondary) — confirms new v1.8 flags
            # are actually contributing even when not top-tier.
            extras = [f for f in r.get("all_flags", [])
                      if f != r.get("flag") and f != r.get("flag2")]
            if extras:
                print(f"           +flags: {', '.join(extras)}")
            # v2.5.2 — TTR PSAR distance always shown
            if r.get("ttr_psar") is not None and r.get("ttr_dist_pct") is not None:
                pos = r.get("ttr_position", "?")
                dist = r["ttr_dist_pct"]
                # Visual cue: caution emoji when <2% from flip
                cue = "⚠️ " if abs(dist) < 2 else ""
                print(f"           TTR: PSAR=${r['ttr_psar']} ({pos} price, dist={dist:+.2f}%) {cue}")
            fund_bits = []
            if r.get("earnings_dte") is not None:
                fund_bits.append(f"earnings={r['earnings_dte']}d")
            if r.get("beneish_m") is not None:
                fund_bits.append(f"M={r['beneish_m']}")
            tf = r.get("thirteen_f")
            if tf:
                if tf.get("inst_pct") is not None:
                    fund_bits.append(f"inst={tf['inst_pct']}%")
                if tf.get("qoq_shares_change_pct") is not None:
                    fund_bits.append(f"13F_qoq={tf['qoq_shares_change_pct']:+.1f}%")
            if r.get("gate"):
                fund_bits.append(f"gate={r['gate']}")
            if fund_bits:
                print(f"           {' | '.join(fund_bits)}")
            if tf and tf.get("top_3"):
                holders = ", ".join(h["holder"][:18] for h in tf["top_3"])
                print(f"           top3: {holders}")
            fa = r.get("femisapien")
            if fa:
                fa_bits = []
                if fa.get("conviction_pct") is not None:
                    fa_bits.append(f"FA_conv={fa['conviction_pct']}%")
                if fa.get("final_signal"):
                    fa_bits.append(f"FA={fa['final_signal']}")
                if fa.get("momentum_class"):
                    fa_bits.append(f"mom={fa['momentum_class']}")
                if fa.get("momentum_exhaustion_label") and fa.get("momentum_exhaustion_score", 0) > 30:
                    fa_bits.append(f"exh={fa['momentum_exhaustion_label']}({fa['momentum_exhaustion_score']})")
                if fa.get("vpin_signal") and fa["vpin_signal"] != "NORMAL":
                    fa_bits.append(f"vpin={fa['vpin_signal']}")
                if fa.get("hgdcf_signal"):
                    fa_bits.append(f"hgdcf={fa['hgdcf_signal']}")
                if fa.get("lppls_bubble") and fa["lppls_bubble"] != "NONE":
                    fa_bits.append(f"lppls={fa['lppls_bubble']}({fa.get('lppls_crash_prob',0):.2f})")
                if fa.get("cross_asset_gate") is False:
                    fa_bits.append("cross_asset=FAIL")
                if fa_bits:
                    print(f"           FA: {' | '.join(fa_bits)}")
                if fa.get("rationale"):
                    print(f"           FA_rationale: {fa['rationale']}")
            # v2.3 — TA Fusion display
            taf = r.get("ta_fusion")
            if taf:
                taf_bits = []
                if taf.get("core_signal"):
                    taf_bits.append(f"core={taf['core_signal']}")
                if taf.get("conviction_pct") is not None:
                    taf_bits.append(f"conv={taf['conviction_pct']}%")
                if taf.get("ema_ribbon"):
                    taf_bits.append(f"ema={taf['ema_ribbon']}")
                if taf.get("hl_cycle_phase"):
                    taf_bits.append(f"cycle={taf['hl_cycle_phase']}({taf.get('hl_cycle_bars',0):.0f})")
                if taf.get("obv_div") and taf["obv_div"].upper() != "NONE":
                    taf_bits.append(f"obv_div={taf['obv_div']}")
                if taf_bits:
                    print(f"           TAF: {' | '.join(taf_bits)}")
            # v2.3 — sentiment display
            sent = r.get("sentiment")
            if sent and sent.get("combined_score") is not None:
                sb = []
                sb.append(f"combined={sent['combined_score']:+.2f}")
                if sent.get("news_score") is not None:
                    sb.append(f"news={sent['news_score']:+.2f}")
                if sent.get("analyst_score") is not None:
                    sb.append(f"analyst={sent['analyst_score']:+.2f}")
                print(f"           SENT: {' | '.join(sb)}")
            # v2.3 — price decomposition display
            pd_sig = r.get("price_decomp")
            if pd_sig:
                pdb = [f"src={pd_sig.get('backend','?')}"]
                for k in ("fair_value", "current_price", "gap_pct", "zscore", "verdict"):
                    if k in pd_sig and pd_sig[k] is not None:
                        v = pd_sig[k]
                        if isinstance(v, float):
                            pdb.append(f"{k}={v:.2f}" if k != "gap_pct" else f"{k}={v:+.1f}%")
                        else:
                            pdb.append(f"{k}={v}")
                if len(pdb) > 1:
                    print(f"           PRICE_DECOMP: {' | '.join(pdb)}")
            # v2.7 displays
            ins = r.get("insider")
            if ins:
                ib = []
                if ins.get("buy_count") is not None:
                    ib.append(f"buys={ins['buy_count']}")
                if ins.get("sell_count") is not None:
                    ib.append(f"sells={ins['sell_count']}")
                if ins.get("net_buys") is not None:
                    ib.append(f"net={ins['net_buys']:+}")
                if ins.get("verdict"):
                    ib.append(f"v={ins['verdict']}")
                if ib:
                    print(f"           INSIDER: {' | '.join(ib)}")
            eps = r.get("eps_rev")
            if eps:
                eb = []
                if eps.get("revision_direction"):
                    eb.append(f"dir={eps['revision_direction']}")
                if eps.get("revision_score") is not None:
                    eb.append(f"score={eps['revision_score']}")
                if eps.get("up_count") is not None and eps.get("down_count") is not None:
                    eb.append(f"up/dn={eps['up_count']}/{eps['down_count']}")
                if eb:
                    print(f"           EPS_REV: {' | '.join(eb)}")
            opts = r.get("opts_flow")
            if opts:
                ob = []
                if opts.get("put_call_ratio") is not None:
                    try: ob.append(f"P/C={float(opts['put_call_ratio']):.2f}")
                    except (ValueError, TypeError): pass
                if opts.get("flow_direction"):
                    ob.append(f"flow={opts['flow_direction']}")
                if opts.get("iv_rank") is not None:
                    try: ob.append(f"IV_rank={float(opts['iv_rank']):.0f}")
                    except (ValueError, TypeError): pass
                if ob:
                    print(f"           OPTIONS: {' | '.join(ob)}")
            # v2.8 displays
            cong = r.get("congress")
            if cong:
                cb = []
                if cong.get("buy_count") is not None:
                    cb.append(f"buys={cong['buy_count']}")
                if cong.get("sell_count") is not None:
                    cb.append(f"sells={cong['sell_count']}")
                if cong.get("net_direction"):
                    cb.append(f"net={cong['net_direction']}")
                if cong.get("notable_trader"):
                    cb.append(f"top={cong['notable_trader']}")
                if cb:
                    print(f"           CONGRESS: {' | '.join(cb)}")
            si = r.get("short_int")
            if si:
                sb = []
                if si.get("short_pct_float") is not None:
                    try: sb.append(f"SI={float(si['short_pct_float']):.1f}%float")
                    except (ValueError, TypeError): pass
                if si.get("days_to_cover") is not None:
                    try: sb.append(f"DTC={float(si['days_to_cover']):.1f}d")
                    except (ValueError, TypeError): pass
                if si.get("borrow_rate") is not None:
                    try: sb.append(f"borrow={float(si['borrow_rate']):.1f}%")
                    except (ValueError, TypeError): pass
                if sb:
                    print(f"           SHORT_INT: {' | '.join(sb)}")
            vola = r.get("vol_anomaly")
            if vola:
                vb = []
                if vola.get("volume_zscore") is not None:
                    try: vb.append(f"z={float(vola['volume_zscore']):+.1f}")
                    except (ValueError, TypeError): pass
                if vola.get("anomaly_type"):
                    vb.append(f"type={vola['anomaly_type']}")
                if vb:
                    print(f"           VOL_ANOMALY: {' | '.join(vb)}")
            adp = r.get("adaptive_pred")
            if adp:
                ab = []
                if adp.get("predicted_return_pct") is not None:
                    try: ab.append(f"pred={float(adp['predicted_return_pct']):+.1f}%")
                    except (ValueError, TypeError): pass
                if adp.get("confidence") is not None:
                    try: ab.append(f"conf={float(adp['confidence']):.0f}%")
                    except (ValueError, TypeError): pass
                if adp.get("direction"):
                    ab.append(f"dir={adp['direction']}")
                if adp.get("model"):
                    ab.append(f"model={adp['model']}")
                if adp.get("horizon_days") is not None:
                    ab.append(f"horizon={adp['horizon_days']}d")
                if ab:
                    print(f"           ADAPTIVE_PRED: {' | '.join(ab)}")

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
