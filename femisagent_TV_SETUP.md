# femisagent × TradingView — webhook integration (v3.0)

TradingView alerts POST JSON to the femisagent MCP server. Each alert records
the state of one indicator on one timeframe for one ticker. The scanner turns
those states into `TV_*` flags (so the live blend can calibrate them) and
applies `/etc/femisagent/tv_policy.json` as a permission layer on top of the
EV score.

## 1. VPS: secret + Funnel path

```bash
# secret for the webhook URL (TradingView cannot send headers)
TVS=$(openssl rand -hex 24)
sed -i '/^FEMISA_TV_SECRET=/d' /etc/femisagent/mcp.env; echo "FEMISA_TV_SECRET=$TVS" >> /etc/femisagent/mcp.env
sed -i '/FEMISA_TV_SECRET=/d' /etc/systemd/system/femisagent_mcp.service.d/env.conf
echo "Environment=FEMISA_TV_SECRET=$TVS" >> /etc/systemd/system/femisagent_mcp.service.d/env.conf
unset TVS
systemctl daemon-reload && systemctl restart femisagent_mcp.service

# expose /tv next to /mcp on the Funnel
tailscale funnel --bg --set-path=/tv http://localhost:8766/tv
tailscale funnel status
```

Webhook URL for TradingView (read the secret with
`grep ^FEMISA_TV_SECRET= /etc/femisagent/mcp.env`):

```
https://srv1355468.tail7d57fe.ts.net/tv/<FEMISA_TV_SECRET>
```

## 2. TradingView alert

Create the alert on the chart/indicator as usual. In the alert dialog:

* **Condition**: your indicator's condition (e.g. "Trend turns bullish").
* **Options**: "Once Per Bar Close" (recommended) — intrabar firings flip
  states back and forth.
* **Notifications → Webhook URL**: the URL above.
* **Message**: JSON. One alert per state you want femisagent to know about.

```json
{"ticker":"{{ticker}}","exchange":"{{exchange}}","indicator":"TREND","timeframe":"{{interval}}","state":"PERMIT","price":{{close}},"bar_time":"{{time}}"}
```

Fields:

| field | required | values |
|---|---|---|
| `ticker` | yes | `{{ticker}}` (also accepts `NASDAQ:NVDA`) |
| `indicator` | yes | your name for the setup: `TREND`, `TRIGGER`, `SQUEEZE` … (uppercased, spaces → `_`) |
| `timeframe` | no | `{{interval}}` → normalised to `1m 5m 15m 30m 1h 2h 4h 1D 1W 1M` (default `1D`) |
| `state` | yes | `PERMIT` (can trade) · `BLOCK` (stand aside) · `ENTRY` · `EXIT`. Synonyms accepted: `GO/GREEN/CAN_TRADE`, `RED/NO_TRADE/WAIT`, `BUY/LONG/ENTER`, `SELL/CLOSE/TAKE_PROFIT`. Anything else is kept as raw text. |
| `direction` | no | `LONG` / `SHORT` |
| `price`, `bar_time`, `message` | no | `{{close}}`, `{{time}}`, free text |

Typical setup: a slow chart (1D or 1W) that emits `PERMIT` / `BLOCK`, and a
fast chart (1h/4h) that emits `ENTRY` / `EXIT`. Make two alerts per indicator
(one per state) or use a Pine `alert()` call that fills `state` dynamically.

## 3. Policy

```bash
cp /docker/openclaw-vhii/data/.openclaw/workspace/scripts/tv_policy.json.example /etc/femisagent/tv_policy.json
nano /etc/femisagent/tv_policy.json
```

* `require_permit` — ALL listed indicator@timeframe must have a fresh
  `PERMIT`/`ENTRY`; otherwise EV is capped at 1.5 (WATCH), gate `TV_NO_PERMIT(...)`.
* `entry_any` — if listed, at least one fresh `ENTRY` is needed for EXECUTE;
  otherwise EV is capped at 3.5 (BUY), gate `TV_NO_ENTRY`.
* `exit_any` — any fresh `EXIT`/`BLOCK` zeroes EV, adds flag `TV_EXIT` and
  gate `TV_EXIT(...)`. Held names with `TV_EXIT` are what the alert dispatcher
  and rebalance engine should act on.
* `max_age_hours` — how long a state stays valid per timeframe (defaults:
  1h→6h, 4h→30h, 1D→80h, 1W→250h). A stale state is ignored.
* No policy file → `TV_*` flags are recorded but nothing is gated.

The scanner reads the policy and cache on each run; no restart needed.

## 4. Verify

```bash
# fake an alert from the VPS itself (replace SECRET)
curl -s -X POST "http://localhost:8766/tv/SECRET" -H "Content-Type: application/json" \
  -d '{"ticker":"NVDA","indicator":"TREND","timeframe":"D","state":"PERMIT","price":210.9}'
# → {"ok": true, "stored": {...}}
cat /var/lib/femisagent/tv_signals.json
```

From Claude: `query_tv_signals(ticker="NVDA")`. In Supabase:
`select * from femisagent_tv_latest;`. After a scan, NVDA's row carries
`TV_TREND_1D_PERMIT` in its flags; after ~4 weeks `query_outcomes_by_flag`
shows the realised 20-day return of each TV state.

## 5. Caveats

* TradingView webhooks need a paid plan (Essential or above).
* Alerts fire only while the alert exists and has not expired (TradingView
  alerts expire after 2 months unless "Open-ended").
* The scanner does not pull from TradingView; if an alert never fires, the
  state is simply absent (treated as "no permit" when `require_permit` lists it).
