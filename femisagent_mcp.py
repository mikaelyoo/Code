#!/usr/bin/env python3
"""
femisagent_mcp.py — MCP server exposing femisagent tools to Claude.

Two modes:
  stdio  (default): Claude Desktop launches as subprocess
  http   --http --port 8766: Claude.ai web/remote connects via URL

Tools exposed (same as the Telegram bot had):
  run_scan(tickers)            — ad-hoc scan, returns verdicts
  query_portfolio()            — held positions w/ latest verdicts
  query_signal_history(ticker) — past scan history for a single name
  query_outcomes_by_flag()     — realized WR/avg_ret per flag (after data accrues)
  query_calibration()          — latest backtest metadata
  propose_rebalance(commit)    — generate BasketTrader CSV + memo

Install:
  pip install mcp

Run (stdio for Claude Desktop):
  python3 femisagent_mcp.py

Run (HTTP for claude.ai web):
  python3 femisagent_mcp.py --http --port 8766
  # Then expose via reverse proxy with TLS, or use ngrok for testing:
  #   ngrok http 8766

Claude Desktop config (~/.config/Claude/claude_desktop_config.json):
{
  "mcpServers": {
    "femisagent": {
      "command": "python3",
      "args": ["/path/to/femisagent_mcp.py"],
      "env": {
        "SUPABASE_URL": "https://...",
        "SUPABASE_KEY": "...",
        "JARVIS_BRIDGE_URL": "http://localhost:8765"
      }
    }
  }
}

Claude.ai web custom connector (Settings → Connectors → Add):
  URL: https://your-server.example.com/mcp
  Transport: Streamable HTTP
"""
import argparse
import json
import logging
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import Tool, TextContent
except ImportError:
    print("Missing dependency: pip install mcp", file=sys.stderr)
    sys.exit(1)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                    stream=sys.stderr)
log = logging.getLogger("femisa_mcp")

SCRIPTS_DIR = Path(os.environ.get(
    "FEMISA_SCRIPTS_DIR",
    "/docker/openclaw-vhii/data/.openclaw/workspace/scripts",
))
LAST_RUN_PATH = Path(os.environ.get(
    "FEMISA_LAST_RUN_PATH",
    "/data/.openclaw/workspace/memory/femisagent_last_run.json",
))
BRIDGE_URL = os.environ.get("JARVIS_BRIDGE_URL", "http://localhost:8765")

# ─── Tool implementations (sync; called from async handlers) ───────────────

def _supabase_get(path, params):
    supa_url = os.environ.get("SUPABASE_URL")
    supa_key = os.environ.get("SUPABASE_KEY")
    if not (supa_url and supa_key):
        return {"error": "SUPABASE_URL/SUPABASE_KEY not set"}
    url = f"{supa_url}/rest/v1/{path}"
    if params:
        url += "?" + "&".join(f"{k}={v}" for k, v in params.items())
    req = urllib.request.Request(
        url,
        headers={
            "apikey": supa_key,
            "Authorization": f"Bearer {supa_key}",
        },
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def _load_last_run() -> dict | None:
    """femisagent.py writes {"run_date", "calibration_source", "signals": [...],
    "summary"}. v1 of this server read "results"/"scan_ts"/"calibration_info",
    keys that never existed, so run_scan reported 0 tickers and
    query_portfolio was always empty."""
    if not LAST_RUN_PATH.exists():
        return None
    with LAST_RUN_PATH.open() as f:
        return json.load(f)


def _row_view(x: dict) -> dict:
    out = {
        "ticker": x.get("symbol"),
        "verdict": x.get("verdict"),
        "ev_score": x.get("ev_score"),
        "primary_flag": x.get("flag"),
        "flags": (x.get("all_flags") or [])[:8],
        "price": x.get("price"),
    }
    if x.get("gate"):
        out["gate"] = x["gate"]
    if x.get("qty"):
        out["qty"] = x["qty"]
        out["avg_cost"] = x.get("avg_cost")
        out["unreal_pct"] = x.get("unreal_pct")
    return out


def _scan_summary(run: dict) -> dict:
    sig = run.get("signals") or []
    tiers: dict[str, list] = {}
    for x in sig:
        v = x.get("verdict") or ""
        tier = next((t for t in ("EXECUTE", "BUY", "WATCH", "AVOID") if t in v), "OTHER")
        tiers.setdefault(tier, []).append(_row_view(x))
    for t in tiers:
        tiers[t].sort(key=lambda r: -(r.get("ev_score") or 0))
    return {
        "run_date": run.get("run_date"),
        "calibration": run.get("calibration_source"),
        "scanned": len(sig),
        "counts": {t: len(v) for t, v in tiers.items()},
        "by_tier": tiers,
    }


# ── Background scan jobs ─────────────────────────────────────────────────────
# A scan takes ~1 min per ticker (gate-layer lookups). The MCP client's tool
# timeout is 60 s, and v1 ran the scanner with subprocess.run() *inside the
# async handler*, which blocked the event loop for every other request — one
# 14-ticker call froze the whole server for 15 minutes. Scans now run as a
# detached process; run_scan returns as soon as the job is started (or after
# `wait_seconds` if it finishes sooner) and scan_status polls it.

JOBS_DIR = Path(os.environ.get("FEMISA_JOBS_DIR", "/var/lib/femisagent/jobs"))
_JOBS: dict[str, dict] = {}
_PROGRESS_RE = None


def _progress_from_log(log_path: Path) -> dict:
    global _PROGRESS_RE
    import re
    if _PROGRESS_RE is None:
        _PROGRESS_RE = re.compile(r"^\s*\[(\d+)/(\d+)\]\s+(\S+)")
    done, total, last = 0, None, None
    try:
        text = log_path.read_text(errors="replace")
    except FileNotFoundError:
        return {"done": 0, "total": None, "current": None, "tail": ""}
    for line in text.splitlines():
        m = _PROGRESS_RE.match(line)
        if m:
            done, total, last = int(m.group(1)), int(m.group(2)), m.group(3).rstrip(".")
    return {"done": done, "total": total, "current": last,
            "tail": "\n".join(text.splitlines()[-6:])}


def _running_job() -> dict | None:
    for j in _JOBS.values():
        if j["proc"].poll() is None:
            return j
    return None


SCAN_MAX_MIN = float(os.environ.get("FEMISA_SCAN_MAX_MIN", "45"))


def _kill_job(job: dict, reason: str) -> bool:
    """SIGTERM the scan's whole process group (start_new_session=True made the
    scanner its own group). Returns True if a signal was sent."""
    import signal
    if job["proc"].poll() is not None:
        return False
    try:
        os.killpg(job["proc"].pid, signal.SIGTERM)
        job["killed"] = reason
        log.warning(f"scan job {job['id']} killed: {reason}")
        return True
    except ProcessLookupError:
        return False


def tool_cancel_scan(job_id: str | None = None) -> dict:
    job = _JOBS.get(job_id) if job_id else _running_job()
    if not job:
        return {"error": "no running scan" if not job_id else f"unknown job_id {job_id}"}
    sent = _kill_job(job, "cancel_scan")
    time.sleep(1)
    return {"job_id": job["id"], "signalled": sent, **_job_status(job)}


def _job_status(job: dict) -> dict:
    # Auto-kill a job that has run past SCAN_MAX_MIN (a hung bridge/TWS call
    # otherwise blocks every later run_scan, since only one job may run).
    if job["proc"].poll() is None and "t0" in job \
            and time.time() - job["t0"] > SCAN_MAX_MIN * 60:
        _kill_job(job, f"exceeded FEMISA_SCAN_MAX_MIN={SCAN_MAX_MIN:g}")
    rc = job["proc"].poll()
    st = {
        "job_id": job["id"],
        "tickers": job["tickers"],
        "started": job["started"],
        "elapsed_s": int(time.time() - job["t0"]) if "t0" in job else None,
        "state": ("running" if rc is None else
                  ("finished" if rc == 0 else
                   f"killed({job['killed']})" if job.get("killed") else f"failed(rc={rc})")),
        "log": str(job["log"]),
        **_progress_from_log(job["log"]),
    }
    if rc == 0:
        run = _load_last_run()
        if run:
            st["result"] = _scan_summary(run)
    return st


def tool_run_scan(tickers: list[str], wait_seconds: int = 45) -> dict:
    tickers = [t.strip().upper() for t in (tickers or []) if t and t.strip()]
    if not tickers:
        return {"error": "no tickers provided"}
    cur = _running_job()
    if cur:
        return {"error": "a scan is already running — poll scan_status, then retry",
                **_job_status(cur)}
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    job_id = time.strftime("%Y%m%dT%H%M%S")
    log_path = JOBS_DIR / f"scan_{job_id}.log"
    # System python3, not this server's venv: the scanner's deps (ib_insync,
    # yfinance, pandas) live in the system interpreter, same as the cron.
    # --portfolio: union held names in and annotate qty/avg_cost (the cron does
    # this; v1 of this tool did not, so MCP scans could never show holdings).
    cmd = [os.environ.get("FEMISA_SCAN_PYTHON", "python3"), "-u",
           str(SCRIPTS_DIR / "femisagent.py"), "--portfolio", "--tickers"] + tickers
    log_fh = log_path.open("w")
    proc = subprocess.Popen(
        cmd, cwd=str(SCRIPTS_DIR), stdout=log_fh, stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL, start_new_session=True)
    job = {"id": job_id, "proc": proc, "tickers": tickers, "log": log_path,
           "log_fh": log_fh, "started": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "t0": time.time()}
    _JOBS[job_id] = job
    log.info(f"scan job {job_id} started: {len(tickers)} tickers, pid {proc.pid}")
    deadline = time.time() + max(0, min(int(wait_seconds or 0), 50))
    while time.time() < deadline and proc.poll() is None:
        time.sleep(1)
    st = _job_status(job)
    if st["state"] == "running":
        st["hint"] = (f"Scan takes ~1 min per ticker ({len(tickers)} tickers). "
                      f"Call scan_status(job_id='{job_id}') to poll; the result is "
                      f"attached when state == 'finished'.")
    return st


POSITIONS_CACHE = Path(os.environ.get("FEMISA_POSITIONS_CACHE",
                                      "/var/lib/femisagent/positions_cache.json"))


def tool_sync_positions(positions: list[dict], source: str = "claude-ibkr-connector") -> dict:
    """Write held positions into the scanner's positions cache. femisagent.py
    (v2.9.2+) falls back to this file whenever the bridge/TWS returns no
    positions, so a Claude session that can read the IBKR account (via the
    IBKR connector) becomes the position feed when TWS on the Mac is closed."""
    rows = []
    for p in positions or []:
        sym = str(p.get("symbol") or p.get("ticker") or "").strip().upper()
        try:
            qty = float(p.get("qty") if p.get("qty") is not None else p.get("position") or 0)
            cost = float(p.get("avg_cost") if p.get("avg_cost") is not None else p.get("average_price") or 0)
        except (TypeError, ValueError):
            continue
        if sym and qty:
            rows.append([sym, p.get("exchange") or "SMART", p.get("currency") or "USD", qty, cost])
    if not rows:
        return {"error": "no valid positions (need symbol + non-zero qty)"}
    POSITIONS_CACHE.parent.mkdir(parents=True, exist_ok=True)
    POSITIONS_CACHE.write_text(json.dumps({
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "source": source, "positions": rows}))
    return {"written": len(rows), "path": str(POSITIONS_CACHE),
            "tickers": [r[0] for r in rows],
            "note": "Used by scans when the bridge/TWS returns no positions. Pass --portfolio scans (cron does; run_scan does)."}


def tool_scan_status(job_id: str | None = None) -> dict:
    if not _JOBS:
        run = _load_last_run()
        return {"state": "no jobs in this server process",
                "last_run": _scan_summary(run) if run else None}
    job = _JOBS.get(job_id) if job_id else _JOBS[max(_JOBS)]
    if not job:
        return {"error": f"unknown job_id {job_id}", "known": sorted(_JOBS)}
    return _job_status(job)


def tool_query_portfolio() -> dict:
    run = _load_last_run()
    if not run:
        return {"error": "no recent scan available — run a scan first"}
    sig = run.get("signals") or []
    held = [r for r in sig if r.get("qty")]
    out = {
        "run_date": run.get("run_date"),
        "calibration": run.get("calibration_source"),
        "scanned": len(sig),
        "held_count": len(held),
        "positions": sorted((_row_view(r) for r in held),
                            key=lambda r: -(r.get("ev_score") or 0)),
    }
    if not held:
        out["note"] = ("No held positions in the last scan. Either the book is flat, "
                       "or TWS on the Mac was closed so the bridge returned no "
                       "positions (see 'Watchlist union' / 'cached positions' lines "
                       "in the scan log).")
    return out


def tool_query_signal_history(ticker: str, days: int = 30) -> dict:
    try:
        rows = _supabase_get(
            "femisagent_signal_outcomes",
            {
                "select": "scan_ts,verdict_tier,ev_score,flags,ret_t5d_pct,ret_t20d_pct",
                "ticker": f"eq.{ticker.upper()}",
                "order": "scan_ts.desc",
                "limit": "50",
            },
        )
        return {"ticker": ticker.upper(), "rows": rows[:days] if isinstance(rows, list) else []}
    except Exception as e:
        return {"error": str(e)}


def tool_query_outcomes_by_flag(min_n: int = 20, horizon: str = "t20d") -> dict:
    ret_col = f"ret_{horizon}_pct"
    resolved_col = f"resolved_{horizon}"
    try:
        rows = _supabase_get(
            "femisagent_signal_outcomes",
            {
                "select": f"flags,{ret_col}",
                resolved_col: "eq.true",
                "limit": "5000",
            },
        )
    except Exception as e:
        return {"error": str(e)}
    if not rows or not isinstance(rows, list):
        return {"error": "no resolved outcomes yet (need ~28 days of accumulated data)"}
    flag_stats = {}
    for r in rows:
        ret = r.get(ret_col)
        if ret is None:
            continue
        for flag in r.get("flags") or []:
            flag_stats.setdefault(flag, []).append(float(ret))
    summary = []
    for flag, rets in flag_stats.items():
        if len(rets) < min_n:
            continue
        avg = sum(rets) / len(rets)
        wr = sum(1 for r in rets if r > 0) / len(rets) * 100
        summary.append({
            "flag": flag, "n": len(rets),
            "wr_pct": round(wr, 1), "avg_ret_pct": round(avg, 2),
        })
    summary.sort(key=lambda x: x["avg_ret_pct"], reverse=True)
    return {"horizon": horizon, "min_n": min_n, "flags": summary[:30]}


def tool_query_calibration() -> dict:
    try:
        rows = _supabase_get(
            "femisapien_backtest_runs",
            {
                "select": "id,version,run_date,universe_tickers,weighted_win_rate,weighted_avg_return_pct",
                "order": "id.desc",
                "limit": "3",
            },
        )
        return {"calibrations": rows}
    except Exception as e:
        return {"error": str(e)}


def tool_propose_rebalance(commit: bool = False, nav: float | None = None,
                           max_orders: int | None = None) -> dict:
    cmd = ["python3", str(SCRIPTS_DIR / "femisagent_rebalance.py")]
    if commit:
        cmd.append("--commit")
    if nav:
        cmd.extend(["--nav", str(nav)])
    if max_orders:
        cmd.extend(["--max", str(max_orders)])
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=180, cwd=str(SCRIPTS_DIR))
        return {
            "exit_code": r.returncode,
            "output": r.stdout[-8000:],
            "stderr": (r.stderr[-500:] if r.stderr else None),
            "committed": commit,
        }
    except subprocess.TimeoutExpired:
        return {"error": "rebalance timed out"}


# ─── MCP server registration ───────────────────────────────────────────────

server = Server("femisagent")

TOOLS_SCHEMA = [
    Tool(
        name="run_scan",
        description=(
            "Start a femisagent scan (v2.9.x, latest Supabase calibration + live "
            "blend) on specific tickers as a BACKGROUND JOB. Each ticker takes "
            "~1 minute. Returns immediately with job_id and state; if state is "
            "'running', poll scan_status(job_id) every ~60 s until 'finished' — "
            "the result (verdict tier, EV, flags, gate per ticker, grouped by tier) "
            "is attached then. Only one scan runs at a time. Ad-hoc scans are NOT "
            "logged to femisagent_signal_outcomes (the cron scans are)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "tickers": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Stock symbols to scan, e.g. ['NVDA', 'AMD', 'MU']",
                },
                "wait_seconds": {
                    "type": "integer", "default": 45,
                    "description": "Seconds to wait for completion before returning (max 50; keep under the client's 60 s tool timeout)",
                },
            },
            "required": ["tickers"],
        },
    ),
    Tool(
        name="scan_status",
        description=(
            "Progress/result of a background scan started by run_scan. Without "
            "job_id returns the most recent job. Fields: state (running/finished/"
            "failed), done/total tickers, current ticker, log tail; 'result' "
            "(by_tier: EXECUTE/BUY/WATCH/AVOID) once finished."
        ),
        inputSchema={
            "type": "object",
            "properties": {"job_id": {"type": "string"}},
        },
    ),
    Tool(
        name="cancel_scan",
        description="Kill a running background scan (default: the current one). Use when scan_status shows no progress for several minutes, e.g. stuck on 'Fetching portfolio via bridge'.",
        inputSchema={"type": "object", "properties": {"job_id": {"type": "string"}}},
    ),
    Tool(
        name="sync_positions",
        description=(
            "Push the user's current IBKR holdings into the scanner's positions "
            "cache (/var/lib/femisagent/positions_cache.json). Use after reading "
            "positions from the IBKR connector (get_account_positions) so scans "
            "annotate held names with qty/avg_cost even when TWS on the Mac is "
            "closed. Each item: {symbol, qty, avg_cost}; negative qty = short."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "positions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "symbol": {"type": "string"},
                            "qty": {"type": "number"},
                            "avg_cost": {"type": "number"},
                            "currency": {"type": "string"},
                            "exchange": {"type": "string"},
                        },
                        "required": ["symbol", "qty"],
                    },
                },
                "source": {"type": "string", "default": "claude-ibkr-connector"},
            },
            "required": ["positions"],
        },
    ),
    Tool(
        name="query_portfolio",
        description="Get current held positions with latest scan verdicts. Use when asked about 'my book', 'my positions', or 'what do I hold'.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="query_signal_history",
        description="Historical scan results for a single ticker from femisagent_signal_outcomes. Use when asked how a name has signaled over time.",
        inputSchema={
            "type": "object",
            "properties": {
                "ticker": {"type": "string"},
                "days": {"type": "integer", "default": 30},
            },
            "required": ["ticker"],
        },
    ),
    Tool(
        name="query_outcomes_by_flag",
        description="Aggregate realized forward returns per flag (live data, not backtest). Answers 'which flags actually work?' Requires accumulated data (4-6 weeks).",
        inputSchema={
            "type": "object",
            "properties": {
                "min_n": {"type": "integer", "default": 20, "description": "Min sample size per flag"},
                "horizon": {
                    "type": "string",
                    "enum": ["t5d", "t20d", "t60d"],
                    "default": "t20d",
                    "description": "Forward-return horizon to measure",
                },
            },
        },
    ),
    Tool(
        name="query_calibration",
        description="Latest backtest calibration metadata (Wilson-CI flag stats live in flag_stats JSON of returned row).",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="propose_rebalance",
        description=(
            "Build a rebalance proposal from latest scan + portfolio + risk policy. "
            "Returns BasketTrader-compatible CSV + decision memo. Use when asked "
            "'what should I trade?', 'build me orders', 'rebalance my book'. "
            "Set commit=true to persist CSV+memo to /var/lib/femisagent/proposals/."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "commit": {"type": "boolean", "default": False},
                "nav": {"type": "number", "description": "Override NAV estimate in USD"},
                "max_orders": {"type": "integer", "description": "Cap proposed order count"},
            },
        },
    ),
]


@server.list_tools()
async def list_tools() -> list[Tool]:
    return TOOLS_SCHEMA


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any] | None) -> list[TextContent]:
    args = arguments or {}
    impl = {
        "run_scan": tool_run_scan,
        "scan_status": tool_scan_status,
        "cancel_scan": tool_cancel_scan,
        "sync_positions": tool_sync_positions,
        "query_portfolio": tool_query_portfolio,
        "query_signal_history": tool_query_signal_history,
        "query_outcomes_by_flag": tool_query_outcomes_by_flag,
        "query_calibration": tool_query_calibration,
        "propose_rebalance": tool_propose_rebalance,
    }.get(name)
    if not impl:
        result = {"error": f"unknown tool: {name}"}
    else:
        try:
            # Every implementation is synchronous (urllib, subprocess, file
            # I/O). Run it off the event loop so one slow call cannot stall
            # the other sessions' requests.
            import asyncio
            result = await asyncio.to_thread(impl, **args)
        except Exception as e:
            log.exception(f"Tool {name} crashed")
            result = {"error": f"{type(e).__name__}: {e}"}
    return [TextContent(type="text", text=json.dumps(result, indent=2)[:32000])]


# ─── Entry points ──────────────────────────────────────────────────────────

async def run_stdio():
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def run_http(host: str, port: int):
    try:
        from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
        from starlette.applications import Starlette
        from starlette.routing import Mount
        import uvicorn
    except ImportError:
        print("Missing deps for HTTP mode: pip install starlette uvicorn", file=sys.stderr)
        sys.exit(1)

    manager = StreamableHTTPSessionManager(app=server, stateless=False)

    # Bearer-token auth (2026-09-13). The HTTP endpoint is published to the
    # public internet through Tailscale Funnel, and until now accepted any
    # caller: run_scan / query_portfolio / propose_rebalance were open to the
    # world. Set FEMISA_MCP_TOKEN in /etc/femisagent/mcp.env (or the systemd
    # drop-in); every request to /mcp must then carry
    # "Authorization: Bearer <token>". Unset → server still starts (so a
    # localhost-only deployment keeps working) but logs a loud warning.
    import hmac
    token = os.environ.get("FEMISA_MCP_TOKEN", "").strip()
    if not token:
        log.warning("FEMISA_MCP_TOKEN is not set — the MCP HTTP endpoint is "
                    "UNAUTHENTICATED. Do not expose it via Funnel like this.")

    async def handle(scope, receive, send):
        await manager.handle_request(scope, receive, send)

    # StreamableHTTPSessionManager needs its task group started via run();
    # without this lifespan every request failed once a client got past the
    # router.
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def lifespan(_app):
        async with manager.run():
            yield

    inner = Starlette(routes=[Mount("/mcp", app=handle)], lifespan=lifespan)

    async def app(scope, receive, send):
        if scope.get("type") == "http":
            # Starlette answers POST /mcp with 307 → /mcp/ before any mounted
            # handler runs, so the auth check must sit in front of routing,
            # and the redirect itself is absorbed here.
            # Tailscale Funnel (--set-path=/mcp → http://localhost:8766)
            # strips the prefix and forwards "/", so accept that too.
            if scope.get("path") in ("/mcp", "/", ""):
                scope = dict(scope, path="/mcp/", raw_path=b"/mcp/")
            if token:
                hdrs = {k.lower(): v for k, v in (scope.get("headers") or [])}
                auth = hdrs.get(b"authorization", b"").decode("utf-8", "replace")
                if not hmac.compare_digest(auth, f"Bearer {token}"):
                    await send({"type": "http.response.start", "status": 401,
                                "headers": [(b"content-type", b"application/json"),
                                            (b"www-authenticate", b"Bearer")]})
                    await send({"type": "http.response.body",
                                "body": b'{"error":"unauthorized"}'})
                    return
        await inner(scope, receive, send)

    log.info(f"MCP server listening on http://{host}:{port}/mcp "
             f"({'bearer auth ON' if token else 'NO AUTH'})")
    uvicorn.run(app, host=host, port=port, log_level="info")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--http", action="store_true",
                   help="Run HTTP/Streamable server (for claude.ai remote connector)")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8766)
    args = p.parse_args()

    if args.http:
        run_http(args.host, args.port)
    else:
        import asyncio
        asyncio.run(run_stdio())


if __name__ == "__main__":
    main()
