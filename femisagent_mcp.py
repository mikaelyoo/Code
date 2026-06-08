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


def tool_run_scan(tickers: list[str]) -> dict:
    if not tickers:
        return {"error": "no tickers provided"}
    cmd = ["python3", str(SCRIPTS_DIR / "femisagent.py"), "--tickers"] + list(tickers)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=300, cwd=str(SCRIPTS_DIR))
    except subprocess.TimeoutExpired:
        return {"error": "scan timed out (5 min cap)"}
    if LAST_RUN_PATH.exists():
        with LAST_RUN_PATH.open() as f:
            run = json.load(f)
        return {
            "scanned": len(run.get("results", [])),
            "calibration": run.get("calibration_info"),
            "results": [
                {
                    "ticker": x["symbol"],
                    "verdict": x.get("verdict"),
                    "ev_score": x.get("ev_score"),
                    "flags": x.get("all_flags", [])[:6],
                    "price": x.get("price"),
                }
                for x in run.get("results", [])
            ],
        }
    return {"error": "scan ran but no last_run.json found", "stderr": r.stderr[-1500:]}


def tool_query_portfolio() -> dict:
    if not LAST_RUN_PATH.exists():
        return {"error": "no recent scan available — run a scan first"}
    with LAST_RUN_PATH.open() as f:
        run = json.load(f)
    held = [r for r in run.get("results", []) if r.get("qty")]
    return {
        "scan_ts": run.get("scan_ts"),
        "calibration": run.get("calibration_info"),
        "held_count": len(held),
        "positions": [
            {
                "ticker": r["symbol"],
                "qty": r.get("qty"),
                "verdict": r.get("verdict"),
                "ev": r.get("ev_score"),
                "flags": r.get("all_flags", [])[:6],
            }
            for r in held
        ],
    }


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
        description="Run femisagent v2.8.3 scan on specific tickers (uses live calibration id=15). Returns verdict tier, EV, top flags per ticker.",
        inputSchema={
            "type": "object",
            "properties": {
                "tickers": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Stock symbols to scan, e.g. ['NVDA', 'AMD', 'MU']",
                }
            },
            "required": ["tickers"],
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
            result = impl(**args)
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

    async def handle(scope, receive, send):
        await manager.handle_request(scope, receive, send)

    app = Starlette(routes=[Mount("/mcp", app=handle)])
    log.info(f"MCP server listening on http://{host}:{port}/mcp")
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
