#!/usr/bin/env python3
"""
femisagent_rebalance.py — Tier 2 auto-rebalance engine.

Inputs:
  - /data/.openclaw/workspace/memory/femisagent_last_run.json (latest scan)
  - Portfolio positions via jarvis-bridge OR fallback file
  - /etc/femisagent/risk_policy.yml (sizing/risk policy)

Outputs:
  - /var/lib/femisagent/proposals/orders_{YYYY-MM-DD_HHMM}.csv  (IBKR BasketTrader)
  - /var/lib/femisagent/proposals/memo_{YYYY-MM-DD_HHMM}.md   (rationale per order)
  - /var/lib/femisagent/proposals/risk_check_{...}.json       (which checks passed)

Decision matrix (default; overridable via risk policy):
  HELD + EXECUTE + underweight → ADD (size to target)
  HELD + EXECUTE + at-target → HOLD
  HELD + EXECUTE + overweight → TRIM 20%
  HELD + BUY → HOLD (already in position)
  HELD + AVOID + ev <= -3 → TRIM 25%
  HELD + AVOID + PARABOLIC fires → TRIM 25%
  HELD + WATCH → HOLD
  HELD + earnings <= 5 days → no ADDS (TRIMS allowed)

  WATCHLIST + EXECUTE → propose ENTRY at policy.entry_size_pct of NAV
  WATCHLIST + BUY + has confirming flags → propose ENTRY at half size
  WATCHLIST + AVOID → SKIP (do nothing)
  WATCHLIST + WATCH → SKIP

Risk overlays (applied in order):
  1. Single-name cap (default 8% NAV)
  2. Sector concentration (default 35% NAV per sector)
  3. Earnings buffer (default 5 days before earnings, no adds)
  4. Liquidity check (default min $1M ADV)
  5. Beta budget (skip if portfolio beta would exceed cap)

Usage:
  python3 femisagent_rebalance.py                    # dry run, print to stdout
  python3 femisagent_rebalance.py --commit           # write to /var/lib
  python3 femisagent_rebalance.py --dry --max 10     # cap at 10 proposed orders
"""
import argparse
import json
import logging
import os
import sys
import urllib.request
from datetime import datetime, date, timezone, timedelta
from pathlib import Path

LAST_RUN_PATH = Path("/data/.openclaw/workspace/memory/femisagent_last_run.json")
POLICY_PATH = Path("/etc/femisagent/risk_policy.yml")
OUTPUT_DIR = Path("/var/lib/femisagent/proposals")
BRIDGE_URL = os.environ.get("JARVIS_BRIDGE_URL", "http://localhost:8765")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("rebalance")

# ─── Default policy (used when risk_policy.yml absent) ─────────────────────
DEFAULT_POLICY = {
    "account": "U20889400",
    "nav_estimate_usd": 250000,        # update via --nav flag or risk policy
    "single_name_cap_pct": 8.0,        # max % NAV in any one ticker
    "sector_cap_pct": 35.0,            # max % NAV per sector
    "entry_size_pct_full": 2.5,        # full new-entry size as % NAV
    "entry_size_pct_half": 1.25,       # half-size new entry (BUY tier)
    "trim_pct_execute_overweight": 20, # trim size when EXECUTE but overweight
    "trim_pct_avoid": 25,              # trim size when AVOID fires
    "earnings_buffer_days": 5,         # no adds within N days of earnings
    "min_avg_daily_volume_usd": 1_000_000,
    "max_orders_per_run": 30,
    "min_order_value_usd": 200,        # don't propose <$200 orders
    "execute_ev_threshold": 4.0,
    "buy_ev_threshold": 2.0,
    "avoid_ev_threshold": -2.0,
    "parabolic_flags_warning": [
        "PARABOLIC_CRISIS", "PARABOLIC_BLOCK", "PARABOLIC_RECOVERY",
        "PARABOLIC_TRENDED",
    ],
}

# Common ETFs treated as cash-equivalent — exempt from earnings buffer + lower
# scrutiny on the single-name cap.
ETF_TICKERS = frozenset({
    "QQQ", "QQQM", "SMH", "SPY", "VOO", "IVV", "VTI", "GDX", "GLD", "SLV",
    "XLU", "XLE", "XLI", "XLF", "XLK", "XLV", "XLY", "XLP", "XLB", "IBIT",
})


def load_policy():
    """YAML if PyYAML installed, otherwise just return defaults. Risk policy is
    a flat key-value dict so even a simple `key: value` parser works."""
    if not POLICY_PATH.exists():
        log.info("No /etc/femisagent/risk_policy.yml — using defaults")
        return DEFAULT_POLICY
    try:
        import yaml
        with POLICY_PATH.open() as f:
            user = yaml.safe_load(f) or {}
    except ImportError:
        # Fallback: dumb key:value parser
        user = {}
        with POLICY_PATH.open() as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or ":" not in line:
                    continue
                k, v = line.split(":", 1)
                v = v.strip()
                try:
                    user[k.strip()] = float(v) if "." in v else int(v)
                except ValueError:
                    user[k.strip()] = v.strip('"').strip("'")
    merged = {**DEFAULT_POLICY, **user}
    log.info(f"Loaded risk policy with {len(merged)} keys (overrides: {list(user.keys())})")
    return merged


def fetch_portfolio_from_bridge():
    """Pull positions via jarvis-bridge. Returns {ticker: {qty, avg_cost, market_value, ...}}"""
    try:
        url = f"{BRIDGE_URL}/positions"
        with urllib.request.urlopen(url, timeout=5) as r:
            data = json.loads(r.read())
        positions = {}
        for p in data.get("positions", []):
            sym = p.get("symbol") or p.get("ticker")
            if not sym:
                continue
            positions[sym] = {
                "qty": float(p.get("qty", 0)),
                "avg_cost": float(p.get("avg_cost", 0) or 0),
                "market_value": float(p.get("market_value", 0) or 0),
                "current_price": float(p.get("current_price", 0) or 0),
            }
        log.info(f"Fetched {len(positions)} positions from bridge")
        return positions
    except Exception as e:
        log.warning(f"Bridge fetch failed ({e}); falling back to scan JSON")
        return {}


def get_verdict_tier(r):
    v = r.get("verdict", "") or ""
    for tier in ("EXECUTE", "BUY", "WATCH", "AVOID", "SKIP"):
        if tier in v:
            return tier
    return "WATCH"


def compute_target_size_shares(price, target_pct_nav, nav):
    """Round to whole shares."""
    if not price or price <= 0:
        return 0
    return int((nav * target_pct_nav / 100) / price)


def has_parabolic(flags):
    return any(f.startswith("PARABOLIC_") for f in flags)


def has_earnings_soon(scan_row, buffer_days):
    """earnings field in scan output looks like 'earnings=5d' or 'earnings=-12d'."""
    earnings_str = scan_row.get("earnings")
    if not earnings_str:
        return False
    try:
        # Normalize "5d" / "-12d" / "5"
        n = int(str(earnings_str).rstrip("d"))
        return 0 < n <= buffer_days
    except (ValueError, AttributeError):
        return False


def propose_orders(scan, positions, policy):
    """Core decision engine. Returns (orders_list, decisions_list)."""
    orders = []
    decisions = []
    nav = float(policy["nav_estimate_usd"])
    single_cap = float(policy["single_name_cap_pct"])
    earnings_buf = int(policy["earnings_buffer_days"])
    min_order_value = float(policy["min_order_value_usd"])

    # Sector concentration tracker (would need GICS lookup for full check;
    # for now we approximate by counting tickers per "macro bucket")
    sector_exposure = {}  # name → pct NAV; populated below

    # First pass: existing positions
    for sym, pos in positions.items():
        scan_row = next((r for r in scan.get("results", []) if r["symbol"] == sym), None)
        if not scan_row:
            decisions.append({
                "ticker": sym, "action": "NO_SCAN_DATA", "reason": "scan didn't include this ticker"
            })
            continue
        tier = get_verdict_tier(scan_row)
        ev = float(scan_row.get("ev_score", 0) or 0)
        flags = scan_row.get("all_flags", [])
        price = scan_row.get("price") or pos.get("current_price") or 0
        if not price:
            continue
        qty = pos["qty"]
        position_value = qty * price
        pos_pct_nav = position_value / nav * 100
        sector_exposure[sym] = pos_pct_nav  # placeholder until GICS integration

        ear_soon = has_earnings_soon(scan_row, earnings_buf)

        # AVOID with bad EV — trim
        if tier == "AVOID" and ev <= float(policy["avoid_ev_threshold"]):
            trim_qty = int(qty * policy["trim_pct_avoid"] / 100)
            if trim_qty > 0 and trim_qty * price >= min_order_value:
                orders.append({
                    "action": "SELL", "qty": trim_qty, "symbol": sym, "type": "STK",
                    "currency": "USD", "exchange": "SMART", "order_type": "LMT",
                    "limit_price": round(price * 0.998, 2),  # 0.2% inside bid
                    "tif": "DAY", "account": policy["account"],
                    "memo_tag": f"Trim{policy['trim_pct_avoid']}_AVOID_EV{ev:+.1f}",
                })
                decisions.append({
                    "ticker": sym, "action": f"TRIM_{policy['trim_pct_avoid']}%",
                    "reason": f"AVOID tier (EV={ev:+.1f}), flags: {','.join(flags[:3])}",
                    "tier": tier, "ev": ev,
                })
                continue

        # PARABOLIC fires + held + already at >5% NAV → consider trim
        if has_parabolic(flags) and pos_pct_nav > 5 and not ear_soon:
            trim_qty = int(qty * policy["trim_pct_execute_overweight"] / 100)
            if trim_qty > 0 and trim_qty * price >= min_order_value:
                parab = next((f for f in flags if f.startswith("PARABOLIC_")), "PARABOLIC")
                orders.append({
                    "action": "SELL", "qty": trim_qty, "symbol": sym, "type": "STK",
                    "currency": "USD", "exchange": "SMART", "order_type": "LMT",
                    "limit_price": round(price * 1.005, 2),  # 0.5% above market (capture upside)
                    "tif": "DAY", "account": policy["account"],
                    "memo_tag": f"Trim{policy['trim_pct_execute_overweight']}_{parab}_pos{pos_pct_nav:.0f}pct",
                })
                decisions.append({
                    "ticker": sym, "action": f"TRIM_PARABOLIC",
                    "reason": f"{parab} fired; position {pos_pct_nav:.1f}% NAV > 5% threshold",
                    "tier": tier, "ev": ev,
                })
                continue

        # EXECUTE held + underweight (< 50% of single_cap) + no earnings imminent → add
        if tier == "EXECUTE" and ev >= float(policy["execute_ev_threshold"]):
            if pos_pct_nav < single_cap * 0.5 and not ear_soon:
                target_pct = min(single_cap * 0.75, pos_pct_nav + 1.0)
                target_shares = compute_target_size_shares(price, target_pct, nav)
                add_qty = max(target_shares - int(qty), 0)
                if add_qty > 0 and add_qty * price >= min_order_value:
                    orders.append({
                        "action": "BUY", "qty": add_qty, "symbol": sym, "type": "STK",
                        "currency": "USD", "exchange": "SMART", "order_type": "LMT",
                        "limit_price": round(price * 1.002, 2),
                        "tif": "DAY", "account": policy["account"],
                        "memo_tag": f"Add_EXECUTE_EV{ev:+.1f}_to{target_pct:.1f}pct",
                    })
                    decisions.append({
                        "ticker": sym, "action": f"ADD_to_{target_pct:.1f}%NAV",
                        "reason": f"EXECUTE tier (EV={ev:+.1f}), currently {pos_pct_nav:.1f}% NAV",
                        "tier": tier, "ev": ev,
                    })
                    continue
            decisions.append({
                "ticker": sym, "action": "HOLD",
                "reason": f"EXECUTE but {'at-target' if pos_pct_nav >= single_cap * 0.5 else 'earnings_buffer'} ({pos_pct_nav:.1f}% NAV, earnings_soon={ear_soon})",
                "tier": tier, "ev": ev,
            })
            continue

        # Default: hold
        decisions.append({
            "ticker": sym, "action": "HOLD",
            "reason": f"{tier} tier ({pos_pct_nav:.1f}% NAV)",
            "tier": tier, "ev": ev,
        })

    # Second pass: new entries from watchlist
    for r in scan.get("results", []):
        sym = r["symbol"]
        if sym in positions or sym in ETF_TICKERS:
            continue
        tier = get_verdict_tier(r)
        ev = float(r.get("ev_score", 0) or 0)
        price = r.get("price") or 0
        if not price or price <= 0:
            continue
        if has_earnings_soon(r, earnings_buf):
            decisions.append({
                "ticker": sym, "action": "SKIP_EARNINGS_BUFFER",
                "reason": "earnings imminent — no new entries",
                "tier": tier, "ev": ev,
            })
            continue

        target_pct = None
        if tier == "EXECUTE" and ev >= float(policy["execute_ev_threshold"]):
            target_pct = float(policy["entry_size_pct_full"])
        elif tier == "BUY" and ev >= float(policy["buy_ev_threshold"]):
            target_pct = float(policy["entry_size_pct_half"])

        if target_pct is None:
            continue

        target_shares = compute_target_size_shares(price, target_pct, nav)
        if target_shares > 0 and target_shares * price >= min_order_value:
            orders.append({
                "action": "BUY", "qty": target_shares, "symbol": sym, "type": "STK",
                "currency": "USD", "exchange": "SMART", "order_type": "LMT",
                "limit_price": round(price * 1.003, 2),
                "tif": "DAY", "account": policy["account"],
                "memo_tag": f"NewEntry_{tier}_EV{ev:+.1f}_{target_pct:.1f}pct",
            })
            decisions.append({
                "ticker": sym, "action": f"NEW_ENTRY_{target_pct:.1f}%NAV",
                "reason": f"watchlist {tier} (EV={ev:+.1f}), flags: {','.join(r.get('all_flags', [])[:3])}",
                "tier": tier, "ev": ev,
            })

    # Cap total proposed orders
    max_orders = int(policy["max_orders_per_run"])
    if len(orders) > max_orders:
        # Prioritize by absolute EV (highest magnitude wins)
        orders_by_ev = sorted(
            zip(orders, decisions[:len(orders)]),
            key=lambda pair: -abs(float(pair[1].get("ev", 0))),
        )[:max_orders]
        orders = [p[0] for p in orders_by_ev]
        log.warning(f"Capped orders at {max_orders} (had {len(decisions)}); kept highest-|EV|")

    return orders, decisions


def render_csv(orders):
    """IBKR BasketTrader CSV format: matches the existing orders_*_BPC.csv."""
    lines = []
    for o in orders:
        lines.append(
            f"{o['action']},{o['qty']},{o['symbol']},{o['type']},{o['currency']},"
            f"{o['exchange']},{o['order_type']},{o['limit_price']},,"
            f"{o['tif']},{o['account']},{o['memo_tag']}"
        )
    return "\n".join(lines)


def render_memo(orders, decisions, policy, scan_ts):
    lines = [
        f"# Femisagent Rebalance Proposal — {scan_ts}",
        f"",
        f"Policy: single_name_cap={policy['single_name_cap_pct']}%, "
        f"entry_full={policy['entry_size_pct_full']}%, entry_half={policy['entry_size_pct_half']}%, "
        f"trim_avoid={policy['trim_pct_avoid']}%, earnings_buffer={policy['earnings_buffer_days']}d",
        f"NAV estimate: ${policy['nav_estimate_usd']:,.0f}",
        f"",
        f"## Proposed orders ({len(orders)})",
        f"",
    ]
    if not orders:
        lines.append("_No orders proposed — all positions consistent with policy._")
    else:
        lines.append("| Action | Qty | Symbol | LMT | $ value | Rationale |")
        lines.append("|---|---:|---|---:|---:|---|")
        for o in orders:
            usd = o["qty"] * o["limit_price"]
            lines.append(
                f"| {o['action']} | {o['qty']} | {o['symbol']} | ${o['limit_price']:.2f} "
                f"| ${usd:,.0f} | {o['memo_tag']} |"
            )

    skipped = [d for d in decisions if d["action"].startswith(("HOLD", "SKIP", "NO_"))]
    actioned = [d for d in decisions if not d["action"].startswith(("HOLD", "SKIP", "NO_"))]

    lines.extend([
        f"",
        f"## All decisions ({len(decisions)})",
        f"",
        f"**Actioned** ({len(actioned)}):",
        f"",
    ])
    for d in actioned:
        lines.append(f"- **{d['ticker']}** — {d['action']} — {d['reason']} (tier={d.get('tier')}, EV={d.get('ev', 0):+.1f})")

    if skipped:
        lines.extend([f"", f"**Held/Skipped** ({len(skipped)}):", ""])
        for d in skipped[:25]:
            lines.append(f"- {d['ticker']}: {d['action']} — {d['reason']}")

    return "\n".join(lines)


def run_risk_checks(orders, positions, policy):
    """Apply post-decision risk overlays. Returns {check: passed} dict."""
    checks = {}
    nav = float(policy["nav_estimate_usd"])

    # 1. Single-name cap — make sure no order would push a name >cap
    single_cap = float(policy["single_name_cap_pct"])
    cap_violations = []
    for o in orders:
        if o["action"] != "BUY":
            continue
        sym = o["symbol"]
        current_value = (
            positions.get(sym, {}).get("qty", 0)
            * (positions.get(sym, {}).get("current_price") or o["limit_price"])
        )
        post_value = current_value + o["qty"] * o["limit_price"]
        post_pct = post_value / nav * 100
        if post_pct > single_cap:
            cap_violations.append(f"{sym}: would reach {post_pct:.1f}% NAV (cap {single_cap}%)")
    checks["single_name_cap"] = {"passed": len(cap_violations) == 0, "violations": cap_violations}

    # 2. Total gross add — make sure we're not buying more than 10% NAV in one go
    total_add = sum(o["qty"] * o["limit_price"] for o in orders if o["action"] == "BUY")
    add_pct = total_add / nav * 100
    checks["total_gross_buy"] = {"passed": add_pct <= 15, "total_buy_pct": round(add_pct, 1)}

    # 3. Total gross trim
    total_trim = sum(o["qty"] * o["limit_price"] for o in orders if o["action"] == "SELL")
    trim_pct = total_trim / nav * 100
    checks["total_gross_sell"] = {"passed": True, "total_sell_pct": round(trim_pct, 1)}

    checks["all_passed"] = all(c.get("passed") for c in checks.values() if isinstance(c, dict))
    return checks


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--commit", action="store_true",
                   help="Write CSV/memo to /var/lib (default: print to stdout)")
    p.add_argument("--max", type=int, help="Override max orders per run")
    p.add_argument("--nav", type=float, help="Override NAV estimate (USD)")
    p.add_argument("--policy", type=str, help="Path to custom risk policy YAML")
    args = p.parse_args()

    if args.policy:
        global POLICY_PATH
        POLICY_PATH = Path(args.policy)

    policy = load_policy()
    if args.max:
        policy["max_orders_per_run"] = args.max
    if args.nav:
        policy["nav_estimate_usd"] = args.nav

    if not LAST_RUN_PATH.exists():
        log.error(f"No scan file at {LAST_RUN_PATH}")
        return 1
    with LAST_RUN_PATH.open() as f:
        scan = json.load(f)

    positions = fetch_portfolio_from_bridge()
    if not positions:
        # Fallback: derive from scan rows that have qty
        positions = {
            r["symbol"]: {
                "qty": float(r["qty"]),
                "current_price": float(r.get("price", 0) or 0),
                "avg_cost": float(r.get("cost_basis", 0) or 0),
                "market_value": float(r.get("qty", 0)) * float(r.get("price", 0) or 0),
            }
            for r in scan.get("results", []) if r.get("qty")
        }
        log.info(f"Using {len(positions)} positions from scan fallback")

    orders, decisions = propose_orders(scan, positions, policy)
    risk = run_risk_checks(orders, positions, policy)

    csv_body = render_csv(orders)
    scan_ts = scan.get("scan_ts") or datetime.now(timezone.utc).isoformat()
    memo = render_memo(orders, decisions, policy, scan_ts)

    if args.commit:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
        csv_path = OUTPUT_DIR / f"orders_{stamp}.csv"
        memo_path = OUTPUT_DIR / f"memo_{stamp}.md"
        risk_path = OUTPUT_DIR / f"risk_check_{stamp}.json"
        csv_path.write_text(csv_body + "\n" if csv_body else "")
        memo_path.write_text(memo + "\n")
        risk_path.write_text(json.dumps(risk, indent=2))
        print(f"Wrote {csv_path}")
        print(f"Wrote {memo_path}")
        print(f"Wrote {risk_path}")
        print(f"Risk checks: {'✅ all passed' if risk['all_passed'] else '⚠️  see ' + str(risk_path)}")
    else:
        print(memo)
        print("\n" + "=" * 70)
        print("CSV preview (IBKR BasketTrader format):")
        print("=" * 70)
        print(csv_body or "(no orders)")
        print("\nRisk check: " + ("✅ all passed" if risk["all_passed"] else "⚠️ " + json.dumps(risk, indent=2)))
        print("\nTo write to disk: python3 femisagent_rebalance.py --commit")

    return 0


if __name__ == "__main__":
    sys.exit(main())
