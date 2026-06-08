#!/usr/bin/env python3
"""
femisagent_chat.py — Tier 3 conversational agent.

Long-running Telegram bot. Polls for incoming messages. For each user message:
  1. Loads recent chat history from Supabase (last 20 turns of this session)
  2. Calls Claude API (claude-sonnet-4-6 by default — fast + cheap) with tools:
       - run_scan(tickers)          → invokes femisagent.py for ad-hoc analysis
       - query_portfolio()          → reads /data/.openclaw/.../memory/femisagent_last_run.json
       - query_signal_history(...)  → SELECT from femisagent_signal_outcomes
       - query_outcomes_by_flag(...)→ aggregate WR/avg_ret by flag from outcomes table
       - query_calibration(...)     → reads femisapien_backtest_runs
       - search_news(query)         → calls news_radar.py
  3. Logs prompt+response+tool calls to femisagent_chat_history
  4. Replies via Telegram

Env vars:
  TELEGRAM_BOT_TOKEN          (required)
  TELEGRAM_CHAT_ID            (restrict to one chat; if absent, allows any)
  OPENROUTER_API_KEY          (required — uses OpenRouter unified gateway)
  SUPABASE_URL, SUPABASE_KEY  (for chat history + signal outcomes)
  LLM_MODEL                   (optional — defaults to deepseek/deepseek-chat-v4-pro)
  OPENROUTER_REFERER          (optional — attribution header)
  OPENROUTER_TITLE            (optional — app name shown in OpenRouter dashboard)

Run as systemd long-running daemon:
  ExecStart=/usr/bin/python3 /scripts/femisagent_chat.py

Usage from Telegram:
  "what changed in my book this morning?"
  "scan AMD KLAC LRCX with current calibration"
  "which signal combo has highest realized win rate last 60 days?"
  "show me PARABOLIC_CRISIS fires in the last week"
"""
import os
import sys
import json
import time
import logging
import subprocess
import urllib.request
import urllib.error
import urllib.parse
from pathlib import Path
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("femisa_chat")

SCRIPTS_DIR = Path("/docker/openclaw-vhii/data/.openclaw/workspace/scripts")
LAST_RUN_PATH = Path("/data/.openclaw/workspace/memory/femisagent_last_run.json")
MODEL = os.environ.get("LLM_MODEL", "deepseek/deepseek-chat-v4-pro")
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
POLL_INTERVAL = 3

SYSTEM_PROMPT = """You are femisagent, an autonomous trading-analysis assistant for a portfolio
manager. You have access to a 19-engine signal scanner (femisagent.py), a 71-ticker
universe backtest calibrated weekly with Wilson-CI per-flag stats, and a growing
log of realized signal outcomes.

Your job is to answer questions about the user's portfolio and watchlist
grounded in the latest scan + calibration data. Use tools rather than guessing:
  - For "what does X look like now?" → run_scan(tickers=[X])
  - For "how has X done historically?" → query_signal_history(ticker=X)
  - For "which flags actually work?" → query_outcomes_by_flag()

Be concise. Use markdown formatting for Telegram. Cite specific EVs, flag names,
and excess_ret numbers from the calibration (id=15 is the active model).

Never recommend a trade without showing the supporting signals. Never invent
ticker prices or flag stats — always query.
"""

# ─── Tools the LLM can call ────────────────────────────────────────────────

def tool_run_scan(tickers):
    """Run femisagent.py on a list of tickers, return parsed verdict summary."""
    if not tickers:
        return {"error": "no tickers provided"}
    cmd = ["python3", str(SCRIPTS_DIR / "femisagent.py"), "--tickers"] + list(tickers)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=300, cwd=str(SCRIPTS_DIR))
    except subprocess.TimeoutExpired:
        return {"error": "scan timed out"}
    if LAST_RUN_PATH.exists():
        with LAST_RUN_PATH.open() as f:
            run = json.load(f)
        return {
            "scanned": len(run.get("results", [])),
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
    return {"stderr": r.stderr[-2000:], "stdout_tail": r.stdout[-2000:]}


def tool_query_portfolio():
    if not LAST_RUN_PATH.exists():
        return {"error": "no recent scan available"}
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


def _supabase_get(path, params):
    url = f"{os.environ['SUPABASE_URL']}/rest/v1/{path}"
    if params:
        url += "?" + "&".join(f"{k}={v}" for k, v in params.items())
    req = urllib.request.Request(
        url,
        headers={
            "apikey": os.environ["SUPABASE_KEY"],
            "Authorization": f"Bearer {os.environ['SUPABASE_KEY']}",
        },
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def tool_query_signal_history(ticker, days=30):
    rows = _supabase_get(
        "femisagent_signal_outcomes",
        {
            "select": "scan_ts,verdict_tier,ev_score,flags,ret_t5d_pct,ret_t20d_pct",
            "ticker": f"eq.{ticker.upper()}",
            "order": "scan_ts.desc",
            "limit": "50",
        },
    )
    return {"ticker": ticker, "rows": rows[:days]}


def tool_query_outcomes_by_flag(min_n=20, horizon="t20d"):
    """Aggregate realized outcomes by flag — the 'is this flag actually working
    in the live market?' question."""
    ret_col = f"ret_{horizon}_pct"
    resolved_col = f"resolved_{horizon}"
    rows = _supabase_get(
        "femisagent_signal_outcomes",
        {
            "select": f"flags,{ret_col}",
            resolved_col: "eq.true",
            "limit": "5000",
        },
    )
    if not rows:
        return {"error": "no resolved outcomes yet (need ~28 days of accumulated data)"}
    flag_stats = {}
    for r in rows:
        ret = r.get(ret_col)
        if ret is None:
            continue
        for flag in r.get("flags", []):
            flag_stats.setdefault(flag, []).append(float(ret))
    summary = []
    for flag, rets in flag_stats.items():
        if len(rets) < min_n:
            continue
        avg = sum(rets) / len(rets)
        wr = sum(1 for r in rets if r > 0) / len(rets) * 100
        summary.append({"flag": flag, "n": len(rets), "wr_pct": round(wr, 1), "avg_ret_pct": round(avg, 2)})
    summary.sort(key=lambda x: x["avg_ret_pct"], reverse=True)
    return {"horizon": horizon, "min_n": min_n, "flags": summary[:30]}


def tool_query_calibration():
    rows = _supabase_get(
        "femisapien_backtest_runs",
        {
            "select": "id,version,run_date,universe_tickers,weighted_win_rate,weighted_avg_return_pct",
            "order": "id.desc",
            "limit": "3",
        },
    )
    return {"calibrations": rows}


def tool_propose_rebalance(commit=False, nav=None, max_orders=None):
    """Tier 2 — run femisagent_rebalance.py against latest scan + portfolio +
    risk policy. Returns proposed orders + markdown memo. If commit=True,
    persists CSV + memo to /var/lib/femisagent/proposals/."""
    cmd = ["python3", str(SCRIPTS_DIR / "femisagent_rebalance.py")]
    if commit:
        cmd.append("--commit")
    if nav:
        cmd.extend(["--nav", str(nav)])
    if max_orders:
        cmd.extend(["--max", str(max_orders)])
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120, cwd=str(SCRIPTS_DIR))
        return {
            "exit_code": r.returncode,
            "output": r.stdout[-6000:],
            "stderr": r.stderr[-500:] if r.stderr else None,
            "committed": commit,
        }
    except subprocess.TimeoutExpired:
        return {"error": "rebalance timed out"}


TOOLS = [
    {
        "name": "run_scan",
        "description": "Run femisagent v2.8.3 scan on specific tickers. Returns verdicts, EVs, top flags.",
        "input_schema": {
            "type": "object",
            "properties": {"tickers": {"type": "array", "items": {"type": "string"}}},
            "required": ["tickers"],
        },
    },
    {
        "name": "query_portfolio",
        "description": "Get the user's current held positions with latest scan verdicts. Use when asked about 'my book' or 'positions'.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "query_signal_history",
        "description": "Historical scan results for a single ticker. Use when asked how X has signaled over time.",
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string"},
                "days": {"type": "integer", "default": 30},
            },
            "required": ["ticker"],
        },
    },
    {
        "name": "query_outcomes_by_flag",
        "description": "Aggregate realized forward returns by flag. Answers 'which flags actually work?'. Requires accumulated outcomes data (4-6 weeks).",
        "input_schema": {
            "type": "object",
            "properties": {
                "min_n": {"type": "integer", "default": 20},
                "horizon": {"type": "string", "enum": ["t5d", "t20d", "t60d"], "default": "t20d"},
            },
        },
    },
    {
        "name": "query_calibration",
        "description": "Latest backtest calibration metadata (Wilson-CI flag stats live in this row's flag_stats JSON).",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "propose_rebalance",
        "description": "Tier 2: build a rebalance proposal from latest scan + portfolio + risk policy. Returns BasketTrader-compatible CSV orders + decision memo. Use when user asks 'what should I trade?', 'build me orders', 'rebalance my book', etc. Default returns proposal WITHOUT writing to disk (commit=false). Set commit=true to save to /var/lib/femisagent/proposals/ for actual execution.",
        "input_schema": {
            "type": "object",
            "properties": {
                "commit": {"type": "boolean", "default": False,
                           "description": "If true, persist CSV+memo to disk. If false, just return the proposal."},
                "nav": {"type": "number",
                        "description": "Override NAV estimate in USD (default: from risk policy)."},
                "max_orders": {"type": "integer",
                               "description": "Cap proposed order count (default: 30)."},
            },
        },
    },
]

TOOL_IMPL = {
    "run_scan": tool_run_scan,
    "query_portfolio": tool_query_portfolio,
    "query_signal_history": tool_query_signal_history,
    "query_outcomes_by_flag": tool_query_outcomes_by_flag,
    "query_calibration": tool_query_calibration,
    "propose_rebalance": tool_propose_rebalance,
}

# ─── OpenRouter API (OpenAI-compatible chat completions) ───────────────────

def _tools_to_openai_schema():
    """Convert Anthropic-style TOOLS to OpenAI function-calling format."""
    out = []
    for t in TOOLS:
        out.append({
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["input_schema"],
            },
        })
    return out


def llm_call(messages):
    """Call OpenRouter (OpenAI chat-completions compatible).
    `messages` is OpenAI-format: list of {role, content} where role ∈
    {system, user, assistant, tool} and assistant turns may include
    `tool_calls` array."""
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        return {"error": "OPENROUTER_API_KEY not set"}
    payload = {
        "model": MODEL,
        "messages": messages,
        "tools": _tools_to_openai_schema(),
        "tool_choice": "auto",
        "max_tokens": 2000,
        "temperature": 0.2,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": os.environ.get("OPENROUTER_REFERER", "https://femisagent.local"),
        "X-Title": os.environ.get("OPENROUTER_TITLE", "femisagent"),
    }
    req = urllib.request.Request(
        OPENROUTER_URL,
        data=json.dumps(payload).encode(),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:1000]
        log.error(f"OpenRouter HTTP {e.code}: {body}")
        return {"error": f"HTTP {e.code}: {body[:400]}"}
    except Exception as e:
        log.error(f"OpenRouter call failed: {e}")
        return {"error": str(e)}


def run_agent_loop(user_message, session_id):
    """Multi-turn agent loop in OpenAI/OpenRouter format.

    OpenAI message shapes:
      {role:"system", content: SYSTEM_PROMPT}
      {role:"user", content: "..."}
      {role:"assistant", content: "..."|null, tool_calls: [{id, type:"function",
                                                            function:{name, arguments}}]}
      {role:"tool", tool_call_id: "...", content: "..."}
    """
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_message},
    ]
    log_chat(session_id, "user", user_message)

    for hop in range(8):  # safety cap
        resp = llm_call(messages)
        if "error" in resp:
            return f"⚠️ Error: {resp['error']}"

        choices = resp.get("choices") or []
        if not choices:
            return f"⚠️ Empty response from model: {json.dumps(resp)[:400]}"
        msg = choices[0].get("message", {}) or {}

        # Append assistant turn (preserve full shape for next-hop context)
        assistant_turn = {"role": "assistant", "content": msg.get("content")}
        if msg.get("tool_calls"):
            assistant_turn["tool_calls"] = msg["tool_calls"]
        messages.append(assistant_turn)

        tool_calls = msg.get("tool_calls") or []
        if not tool_calls:
            reply = (msg.get("content") or "").strip() or "(empty reply)"
            usage = resp.get("usage", {})
            log_chat(session_id, "assistant", reply,
                     tokens_in=usage.get("prompt_tokens"),
                     tokens_out=usage.get("completion_tokens"))
            return reply

        # Execute each tool call, feed back as role="tool" messages
        for tc in tool_calls:
            fn = tc.get("function", {})
            tool_name = fn.get("name")
            raw_args = fn.get("arguments", "{}")
            try:
                tool_input = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
            except json.JSONDecodeError:
                tool_input = {}
            log.info(f"Tool call: {tool_name}({tool_input})")
            impl = TOOL_IMPL.get(tool_name)
            if not impl:
                result = {"error": f"unknown tool {tool_name}"}
            else:
                try:
                    result = impl(**tool_input)
                except Exception as e:
                    result = {"error": f"{type(e).__name__}: {e}"}
            log_chat(session_id, "tool", json.dumps(result)[:1000],
                     tool_name=tool_name, tool_input=tool_input, tool_output=result)
            messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": json.dumps(result)[:8000],
            })

    return "⚠️ Reached max tool-use hops without conclusion."


# ─── Supabase chat history ─────────────────────────────────────────────────

def log_chat(session_id, role, content, tool_name=None, tool_input=None, tool_output=None, tokens_in=None, tokens_out=None):
    supa_url = os.environ.get("SUPABASE_URL")
    supa_key = os.environ.get("SUPABASE_KEY")
    if not (supa_url and supa_key):
        return
    payload = {
        "session_id": session_id,
        "role": role,
        "content": content[:8000],
        "tool_name": tool_name,
        "tool_input": tool_input,
        "tool_output": tool_output,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
    }
    req = urllib.request.Request(
        f"{supa_url}/rest/v1/femisagent_chat_history",
        data=json.dumps(payload).encode(),
        headers={
            "apikey": supa_key,
            "Authorization": f"Bearer {supa_key}",
            "Content-Type": "application/json",
            "Prefer": "return=minimal",
        },
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=5)
    except Exception as e:
        log.debug(f"chat log insert failed: {e}")


# ─── Telegram polling loop ─────────────────────────────────────────────────

def telegram_get_updates(offset):
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    url = f"https://api.telegram.org/bot{token}/getUpdates?timeout=25&offset={offset}"
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            return json.loads(r.read())
    except Exception as e:
        log.warning(f"getUpdates failed: {e}")
        return {"result": []}


def telegram_send(chat_id, text):
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": text[:4000],
        "parse_mode": "Markdown",
    }).encode()
    try:
        urllib.request.urlopen(url, data=data, timeout=10)
    except Exception as e:
        log.warning(f"sendMessage failed: {e}")


def main():
    if not os.environ.get("TELEGRAM_BOT_TOKEN"):
        log.error("TELEGRAM_BOT_TOKEN missing")
        return 1
    if not os.environ.get("OPENROUTER_API_KEY"):
        log.error("OPENROUTER_API_KEY missing")
        return 1

    allowed_chat = os.environ.get("TELEGRAM_CHAT_ID")
    log.info(f"femisagent_chat starting (model={MODEL}, allowed_chat={allowed_chat or 'ANY'})")

    offset = 0
    while True:
        upd = telegram_get_updates(offset)
        for u in upd.get("result", []):
            offset = u["update_id"] + 1
            msg = u.get("message") or u.get("edited_message")
            if not msg or "text" not in msg:
                continue
            chat_id = str(msg["chat"]["id"])
            if allowed_chat and chat_id != allowed_chat:
                log.info(f"Ignoring message from non-allowed chat {chat_id}")
                continue
            text = msg["text"].strip()
            log.info(f"User: {text[:120]}")
            try:
                reply = run_agent_loop(text, session_id=chat_id)
            except Exception as e:
                log.exception("agent loop crashed")
                reply = f"⚠️ Agent error: {type(e).__name__}: {e}"
            telegram_send(chat_id, reply)
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    sys.exit(main())
