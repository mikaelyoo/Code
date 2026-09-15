# Femisagent MCP — Claude integration

Two ways to chat with femisagent using Claude (NO Telegram needed).

## Path 1: Claude Desktop (laptop, stdio mode)

Best for: sit-down analysis sessions on your laptop.
No HTTP server, no reverse proxy needed.

### Setup

1. Install Claude Desktop: https://claude.ai/download

2. Edit Claude Desktop config:

   **macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
   **Linux:** `~/.config/Claude/claude_desktop_config.json`
   **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`

3. Add the femisagent MCP server (this assumes you're running it on the
   SAME machine as Claude Desktop):

   ```json
   {
     "mcpServers": {
       "femisagent": {
         "command": "python3",
         "args": [
           "/docker/openclaw-vhii/data/.openclaw/workspace/scripts/femisagent_mcp.py"
         ],
         "env": {
           "SUPABASE_URL": "https://azyxlnbdgehqeifbggef.supabase.co",
           "SUPABASE_KEY": "PASTE_YOUR_SUPABASE_SERVICE_KEY_HERE",
           "JARVIS_BRIDGE_URL": "http://localhost:8765"
         }
       }
     }
   }
   ```

4. Restart Claude Desktop.

5. In any conversation, the femisagent tools become available. Try:
   - "What's in my portfolio right now?"
   - "Run a scan on NVDA, AMD, MU"
   - "Build me today's rebalance"
   - "Which flags have actually worked in the last 30 days?"

### If Claude Desktop isn't on the same machine as the bridge

Use SSH tunneling or path 2 (HTTP mode below).

## Path 2: Claude.ai web/app (mobile-friendly, HTTP mode)

Best for: phone/tablet access from anywhere.

### Setup on the Hostinger box

1. Install Python deps (the installer already does this):
   ```bash
   pip install mcp starlette uvicorn
   ```

2. Start the MCP server (the installer already does this):
   ```bash
   systemctl enable --now femisagent_mcp.service
   curl -s http://localhost:8766/mcp -i  # should respond
   ```

3. Expose via reverse proxy with TLS (claude.ai requires HTTPS).
   Easiest: Caddy auto-TLS.

   ```bash
   # /etc/caddy/Caddyfile
   femisa-mcp.yourdomain.com {
       reverse_proxy localhost:8766
   }
   ```

   Or use Cloudflare Tunnel:
   ```bash
   cloudflared tunnel --url http://localhost:8766
   ```

   For testing only, ngrok:
   ```bash
   ngrok http 8766
   ```

### Setup in Claude.ai (web/app)

Requires Claude Pro/Max.

1. https://claude.ai → Settings → Connectors → Add custom connector
2. Name: `femisagent`
3. URL: `https://femisa-mcp.yourdomain.com/mcp` (or your ngrok URL)
4. Transport: `Streamable HTTP`
5. Save → connect

### Auth (recommended for HTTPS endpoint)

The current MCP server has NO auth. If you expose it publicly, add a
simple bearer token check. Minimum-viable patch:

```python
# In femisagent_mcp.py, run_http(), wrap the manager:
EXPECTED_TOKEN = os.environ.get("MCP_AUTH_TOKEN")

async def handle(scope, receive, send):
    if EXPECTED_TOKEN:
        headers = dict(scope.get("headers", []))
        auth = headers.get(b"authorization", b"").decode()
        if auth != f"Bearer {EXPECTED_TOKEN}":
            await send({"type": "http.response.start", "status": 401, "headers": []})
            await send({"type": "http.response.body", "body": b"Unauthorized"})
            return
    await manager.handle_request(scope, receive, send)
```

Then in `/etc/femisagent/mcp.env`:
```
MCP_AUTH_TOKEN=long-random-secret-here
```

And configure Claude.ai connector to send `Authorization: Bearer <token>` header.

## Tools exposed by the MCP server

| Tool | What it does |
|---|---|
| `run_scan(tickers)` | Ad-hoc scan with calibration id=15. Returns verdicts. |
| `query_portfolio()` | Held positions w/ latest verdicts. |
| `query_signal_history(ticker, days)` | Past scan history per ticker. |
| `query_outcomes_by_flag(min_n, horizon)` | Realized WR/avg_ret per flag (live data). |
| `query_calibration()` | Latest backtest metadata. |
| `propose_rebalance(commit, nav, max_orders)` | Generate BasketTrader CSV + memo. |

## Example sessions

**Quick portfolio check (any device):**
> "Show me my book and flag anything that flipped to AVOID since this morning"

Claude → calls `query_portfolio()` → reads results → calls `query_signal_history()`
on flagged names → composes summary.

**Building a rebalance:**
> "Build me today's rebalance with a $300k NAV and save it"

Claude → calls `propose_rebalance(commit=true, nav=300000)` → returns memo + CSV path.

**Flag performance check (after 4+ weeks of data):**
> "Which signal combos have actually beat baseline in the last 30 days?"

Claude → calls `query_outcomes_by_flag(horizon='t20d')` → ranks flags → identifies
combinations with highest realized excess return.

## Why MCP over Telegram bot

| Aspect | Telegram bot | MCP server |
|---|---|---|
| LLM cost | $1-3/day (OpenRouter) | $0 (uses your Claude subscription) |
| Mobile access | ✅ Telegram app | ✅ Claude.ai mobile app |
| Setup complexity | Bot token + chat_id + webhook | Just an HTTPS endpoint |
| Quality | DeepSeek V4 Pro | Claude Sonnet 4.6 / Opus |
| Tools | 6 custom-coded | Same 6, plus everything Claude has natively |
| Vendor lock-in | OpenRouter API key | None (open MCP standard) |

The MCP path also benefits from Claude's general intelligence — it can
combine femisagent tools with web search, file reading, document analysis
that the bare Telegram bot can't do.
