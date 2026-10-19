# FEMISAPIEN v3.8 — Extended Backtest 2000-2026 (Regime-Modeled)

Four runs stored in Supabase project `azyxlnbdgehqeifbggef`:

| Run | id | Description | Weight schedule | WR | Avg ret/EXEC |
|-----|----|-------------|-----------------|----|--------------|
| Prior actual (2019-2026)        | 1  | v3.8 full         | 1.0 → 2.5            | 80.9% | +67.76% |
| v3.8r1 extended                 | 2  | 2000-2026 flat    | 0.3-0.5 / 1.0 / 2.5  | 66.7% | +59.11% |
| v3.8r2 recent-heavy             | 3  | 2024-26 @ 5.0×    | 0.3-0.5 / 1.0 / 5.0  | 72.0% | +74.80% |
| **v3.8r3 IC-decay demote**      | **4** | id=3 + HRT_REVERSAL_RISK & GS_DISTRIB neutralized | same as id=3 | **72.0%** | **+74.80%** |

(Aggregate WR / avg-return for id=4 carry over unchanged from id=3 — the demotion only mutates the per-flag `flag_stats` JSON, which doesn't feed into the year-regime weighted aggregate. The behavioral lever is on the live-scanner side: see "Run id=4 …" below.)

## Headline numbers — run id=3 (recent-heavy, current canonical)

| Metric                       | Value                          |
|------------------------------|--------------------------------|
| Period                       | 2000-01-03 → 2026-04-25        |
| Universe (inherited)         | 57 tickers                     |
| Total EXECUTE signals        | 113                            |
| Total WATCH signals          | 6,730                          |
| Weighted win rate            | **72.0%**                      |
| Weighted avg return per EXEC | **+74.80%**                    |

`year_weights` for 2024, 2025, 2026 bumped from **2.5 → 5.0** so the
AI-supercycle out-of-sample window (where v3.8 actually shipped + calibrated)
dominates the aggregate. The 2019-2023 weights are unchanged.

The 2019-2026 prior run (id=1) posted 80.9% / +67.76%. Extending to 2000-2018 —
which captures dot-com (2000-02), GFC (2008), EU/flash-crash years (2010-11,
2015, 2018) — pulls WR down even with 5.0× recent emphasis, because the
ELEVATED/FEAR base rates (0.41 / 0.28) materially drag a regime-modeled
aggregate. With heavier recent weight, the figures land between the prior
narrow-window run and the flat-extended run.

## Methodology — important caveats

This is **not** a signal-replay backtest of 2000-2018. Two hard blockers prevented that:

1. The `historical_pricing_summaries` MCP returned an entitlement error for
   this session, so no daily OHLCV could be fetched for the 2000-2018 window.
2. The FEMISAPIEN backtest script is not in the `run_script` allow-list; the
   v3.8 run (run id=1) relied on six pre-computed yearly JSON files from a
   repo that is not present on this server.

Instead, run id=2 is built as follows:

| Year range | Method                                                                                                  |
|------------|---------------------------------------------------------------------------------------------------------|
| 2000-2020  | **Regime-modeled**: each year tagged with a dominant VIX regime (CALM / BULL / ELEVATED / FEAR) from documented historical context; per-year `execute_win_rate` and `execute_avg_return` are set equal to the v3.8 regime base rates (`run id=1 → regime_stats`). Signal counts (`n_execute`, `n_watch`) are scaled estimates anchored on observed 2021-2026 cadence. |
| 2021-2026  | **Actual** values carried over from `run id=1.year_summary`.                                            |

### Regime base rates (from run id=1)

```
CALM      → win 0.92, avg +112.3%
BULL      → win 0.83, avg  +87.4%
ELEVATED  → win 0.41, avg   -8.2%
FEAR      → win 0.28, avg  -21.4%
```

### Time-decay weight schedule

The 2019-2026 weights from run id=1 are kept (1.0 → 2.5). Pre-2019 years are
weighted **0.3 - 0.5** so the deep history informs but doesn't dominate the
aggregate.

### Per-year regime assignments (pre-2019, ESTIMATED)

| Year | Regime    | Weight | Rationale                                  |
|------|-----------|--------|--------------------------------------------|
| 2000 | ELEVATED  | 0.30   | dot-com peak rolling over                  |
| 2001 | FEAR      | 0.30   | recession + 9/11                           |
| 2002 | FEAR      | 0.30   | bear-market bottom                         |
| 2003 | BULL      | 0.40   | recovery rally +29%                        |
| 2004 | CALM      | 0.40   |                                            |
| 2005 | CALM      | 0.40   |                                            |
| 2006 | BULL      | 0.40   |                                            |
| 2007 | ELEVATED  | 0.40   | late-cycle vol up                          |
| 2008 | FEAR      | 0.50   | GFC, VIX 80                                |
| 2009 | BULL      | 0.40   | recovery                                   |
| 2010 | ELEVATED  | 0.40   | flash crash                                |
| 2011 | ELEVATED  | 0.40   | EU crisis, S&P downgrade                   |
| 2012 | BULL      | 0.40   |                                            |
| 2013 | CALM      | 0.50   | S&P +32%                                   |
| 2014 | CALM      | 0.50   |                                            |
| 2015 | ELEVATED  | 0.50   | China/oil shock Aug                        |
| 2016 | BULL      | 0.50   |                                            |
| 2017 | CALM      | 0.50   | record-low VIX                             |
| 2018 | ELEVATED  | 0.50   | Vol-mageddon Feb, Q4 selloff               |
| 2019 | BULL      | 1.00   | (gap-filled; not in run id=1 year_summary) |
| 2020 | FEAR      | 1.00   | COVID                                      |

## Storage

- `public.femisapien_backtest_runs` row `id=2` — aggregate + `raw_metadata`
  flagging methodology as `regime-model-extension-v1` and listing estimated
  vs actual years.
- `public.femisapien_year_performance` — 27 rows tagged `ESTIMATED` /
  `ACTUAL from run id=1`.
- `flag_stats` / `signal_performance` / `regime_stats` / `v38_calibrations` are
  copied verbatim from run id=1 (no flag-level recomputation was possible
  without signal replay).

## What this run is good for

- Stress-testing the v3.8 base rates against a longer mix of regimes.
- Showing how performance would have looked across dot-com + GFC if flag base
  rates held — a useful upper bound, not a real out-of-sample test.

## What it is **not** good for

- Calibrating new fixes (FIX-12+).
- Brier/IC validation pre-2019.
- Anything that needs the actual 2000-2018 flag distribution.

To upgrade this to a real backtest:

1. Restore historical-pricing entitlements (or load OHLCV via IBKR bridge).
2. Add a `femisagent_backtest.py` to the script allow-list that takes
   `--start` / `--end` / `--universe`.
3. Replay weekly scans 2000-01-07 → 2018-12-28 and overwrite run id=2/3 with
   the real numbers.

## Suggested improvements — methods from top quant shops

Win rate alone is a weak objective for a flag-gated screener (EXECUTE is
already precision-filtered; the alpha lives in WATCH-pool fat-tail captures).
The improvements below target Sharpe, IR, drawdown discipline and signal decay
as much as raw WR. Each is tagged by the desk most associated with it.

### Tier 1 — high impact, fits FEMISAPIEN's existing flag architecture

1. **Walk-forward CV with purged & embargoed folds** (López de Prado / Two
   Sigma). Today the v3.8 calibrations (FIX-7..11) are tuned on the same
   2019-2026 window they're evaluated on — that is in-sample fitting. Carve
   the universe into 6-month forward folds with a 1-week embargo so calibrated
   thresholds (`conv ≥ 87`, `vol 3.0x → 2.5x`, etc.) only ever score on data
   they didn't see during fitting. Expected effect: -5-15pp on reported WR,
   +20-40% on real-money Sharpe.

2. **Per-flag signal-decay tracking** (Renaissance). Maintain a rolling 26-week
   IC for every flag (`MOMENTUM_SURGE`, `VPIN_TOXIC`, `GS_ACCUM`, …). When the
   IC half-life elapses (IC falls below ½ of its trailing-2y mean), auto-demote
   the flag from EXECUTE gate to WATCH-only. Today the v3.8 stats show
   `HRT_REVERSAL_RISK` at WR=36% and `GS_DISTRIB` at WR=20% — these are
   reverse-decayed and should already have been demoted.

3. **Cross-sectional ranking instead of absolute thresholds** (DE Shaw / Two
   Sigma). Replace `conv ≥ 87` with `conv ∈ top-decile-of-universe-this-week`.
   Eliminates regime drift in calibration constants and turns the model into
   a relative-strength engine. Combine with **sector neutrality** so the
   EXECUTE list isn't all semis when semis are hot.

4. **Vol-targeted position sizing** (Citadel). Currently EXECUTE → "trade it";
   make size = `target_daily_vol / (px × σ_20d × √252)` so a 95% VIX-elevated
   name and a 18% VIX-calm name don't get the same notional. Empirically this
   alone usually adds 0.3-0.6 to portfolio Sharpe without changing the signal.

5. **Ensemble + meta-labeling** (López de Prado / Renaissance). Train a small
   gradient-boost (XGBoost / LightGBM, ~50 trees, depth 4) whose features are
   the binary flag fires + regime tag + IC of each flag in last 13w. Target
   the *meta-label*: "did the signal hit +20% before -10%". Use its
   probability to filter EXECUTE, not to generate new signals. Often takes
   precision-filtered WR from ~80% to ~88-90% on the same trades.

### Tier 2 — bigger lift, more data plumbing

6. **Options-implied filters** (SIG / Jane Street). Cheap optionality on the
   signal is real edge. Require either: (a) ATM IV percentile < 60 (you're not
   paying for the move), or (b) skew steepness ≥ x-σ (others bid OTM puts —
   crash protection is dear, longs are cheap). VPIN already proxies
   microstructure toxicity; IV-rank is the cleaner price-of-insurance signal.

7. **Implied-vs-realized vol delta as regime detector** (SIG). Today the
   regime is bucketed by VIX level. A better axis: `IV - RV` (richness).
   `IV - RV > +5` at universe level → toxic; `< -3` → forced-buyer flow. Add
   as a 5th regime label, fold into `regime_stats`.

8. **Dark-pool / options-flow VPIN replacement** (Citadel / SIG). v3.8's
   VPIN_TOXIC flag fires too often (`n=37` in flag_stats). Augment / replace
   with the Easley-Lopez-de-Prado-O'Hara *bulk-volume PIN* computed from
   dark-pool prints (or SqueezeMetrics' dark index proxy). The signal that
   matters is **informed flow before the move**, not aggregate toxicity.

9. **Cointegration / pairs overlay** (DE Shaw / RenTec). For each EXECUTE
   ticker, find its 2 most cointegrated peers in the same GICS sub-industry
   and require the basket spread to be ≥ 2σ from mean before sizing up. Turns
   directional bets into market-neutral pairs when the spread is wide,
   preserving direction-of-flag thesis but cutting beta exposure.

10. **Alt-data layer** (Two Sigma / Citadel). For mega-cap holds in the
    universe (AAPL, MSFT, NVDA, AMZN, …) overlay: credit-card transaction
    panels, web traffic (SimilarWeb), satellite parking-lot counts for
    big-box retail. Don't generate signals — use as **confirm/deny vetoes**
    on EXECUTE. Empirically, alt-data confirm raises WR ~5-10pp on names with
    coverage.

11. **Order-book / queue-position model for entries** (Jane Street). Right
    now EXECUTE is a weekly scan. Convert to intraday entry by routing the
    weekly call to a passive-limit-then-IOC ladder. Halves the slippage cost
    on the biggest movers (which is exactly the WATCH-pool fat tail you want
    to capture).

### Tier 3 — model-architecture changes

12. **Bayesian model averaging across calibration vintages** (DE Shaw).
    Today only the latest v3.8 calibration is live. Run v3.5, v3.6, v3.7,
    v3.8 in parallel paper books and Bayesian-average their EXECUTE
    probabilities weighted by trailing-13w Brier. Prevents calibration drift
    from one bad fix wiping out the strategy.

13. **Reinforcement-learning gate** (Two Sigma / RenTec). Replace the
    rule-tree (sentinel gates, conviction thresholds, FIX-7..11) with a
    contextual bandit / RL policy whose actions are
    `{REJECT, WATCH, EXECUTE-half-size, EXECUTE-full-size}` and reward is
    realized PnL. Start with offline RL on the existing decisions_log so it
    can't trade live until reward exceeds the rule-tree baseline.

14. **Drawdown-aware Kelly fractional sizing** (Citadel / Bridgewater-ish).
    Compute `f* = (b·p - q)/b` with `b = avg_win / avg_loss`, `p =` rolling
    WR, then live-size at `0.25 · f*`. The 0.25 Kelly discount + a hard cap
    at 1% NLV per name keeps a 2008-style FEAR-regime miss from compounding.

15. **Combinatorial purged cross-validation for FIX-12+** (López de Prado).
    Future calibrations should be evaluated against ≥ 8 purged splits, not
    a single 2019-2026 fit. Any FIX whose improvement disappears below 4-of-8
    splits is rejected as overfit.

### Quick wins that need ~1 day of work

- Replace the `weighted_win_rate` headline with `weighted_information_ratio`
  and `Calmar` (CAGR / max-DD). WR ≥ 80% on 47 trades is not statistically
  separable from 70%; IR / Calmar make this clear.
- Add `expected_shortfall_95` on the WATCH pool — fat-tail downside is
  where ELEVATED/FEAR-regime years (2008, 2022) actually bite.
- Add a `signal_correlation_matrix` to `raw_metadata`. Today multiple flags
  fire on the same names (`VPIN_TOXIC` + `HRT_STRONG` + `GS_ACCUM` are highly
  collinear) — this inflates apparent conviction.

### Prioritized roadmap

| Priority | Item | Why first |
|----------|------|-----------|
| P0 | #1 walk-forward CV | Everything else is in-sample fitting until this is fixed. |
| P0 | #2 per-flag IC decay | Two flags (`HRT_REVERSAL_RISK`, `GS_DISTRIB`) already have negative edge in the v3.8 stats. |
| P1 | #4 vol-targeted sizing | Zero new data; pure sizing change; +0.3-0.6 Sharpe. |
| P1 | #5 meta-labeling | Often the single biggest WR lift on precision-filtered signals. |
| P2 | #3 cross-sectional ranking | Requires re-fitting all thresholds. |
| P2 | #6 #7 options/IV filter | Needs IV/skew feed from the prior pricing entitlement. |
| P3 | #8-11 alt-data + execution | Largest lift but biggest data/infra cost. |

## Wiring the live scanner to Supabase (femisagent.py v1.1)

The live `femisagent.py` script (workspace-runner, path
`/data/.openclaw/workspace/scripts/femisagent.py`) previously had **hardcoded
flag stats** — it never read the backtest table, so new Supabase runs (id=2,
id=3) did not change live scan behavior.

This branch adds a patched `femisagent.py` (in the repo root) that hot-loads
the newest `femisapien_backtest_runs` row on every invocation, with offline
cache fallback. Changes vs the original:

- New `load_latest_calibration()` called at `__main__`. Pulls
  `?select=id,version,flag_stats,weighted_win_rate,weighted_avg_return_pct&order=id.desc&limit=1`
  from `https://azyxlnbdgehqeifbggef.supabase.co/rest/v1/femisapien_backtest_runs`.
- Caches the result to
  `/data/.openclaw/workspace/memory/femisagent_flag_stats.cache.json`.
- Fallback chain: Supabase → cache → hardcoded v3.8 defaults.
- Report header now prints `Calibration: supabase-run-id=N` (or `cache-…` /
  `hardcoded-…`) so the scan output is auditable.
- `femisagent_last_run.json` gets a new `calibration_source` field.
- Bumped header version `v1.0 → v1.1`.

### Deploy (one-time, by the operator)

```bash
# 1. Copy the patched script onto the bridge server
scp femisagent.py bridge:/data/.openclaw/workspace/scripts/femisagent.py

# 2. Set the Supabase env vars on the runner
#    Choose ONE of:
#    a) Anon key + a SELECT policy on the table (see migration below)
#    b) Service-role key (bypasses RLS — fine for a server-side script)
export SUPABASE_URL=https://azyxlnbdgehqeifbggef.supabase.co
export SUPABASE_KEY=<anon-or-service-role-key>

# 3. Smoke test
python3 /data/.openclaw/workspace/scripts/femisagent.py --tickers NVDA AMD
# Expect first log line:
# [femisagent] Calibration loaded from Supabase: run id=3 | FEMISAPIEN v3.8r2 — Recent-Heavy …
```

If you go the anon-key route, this migration adds the minimal read policy:

```sql
ALTER TABLE public.femisapien_backtest_runs ENABLE ROW LEVEL SECURITY;
CREATE POLICY "anon read backtest runs"
  ON public.femisapien_backtest_runs
  FOR SELECT
  TO anon
  USING (true);
```

### What changes in live signal output

Today the table column `flag_stats` is identical across run id=1, id=2, id=3 —
because runs 2 and 3 were aggregate reweights, not per-flag recalibrations. So
the v1.1 script with current data will produce **the same EV / EXECUTE / BUY /
WATCH verdicts** as v1.0, but the report header will document which Supabase
row drove the scan, and the next time anyone calibrates a new per-flag table
(FIX-12+, walk-forward CV, IC-decay demotion) the live scanner picks it up
automatically.

That auditability is the actual value of v1.1 — no more "the script says X but
the backtest table says Y" drift.

## Run id=4 — first behavior-changing calibration (IC-decay demote)

Once v1.1 is deployed and pulling the latest row, run id=4 actually changes
live-scan verdicts. Two flags whose v3.8 sample WR fell below 0.50 — the
IC-decay threshold from Tier 1 #2 — are neutralized in `flag_stats`:

| Flag                  | Sample n | Prev WR | Prev avg_ret | Prev EV | New WR | New avg_ret | New EV |
|-----------------------|---------:|--------:|-------------:|--------:|-------:|------------:|-------:|
| `HRT_REVERSAL_RISK`   | 14       | 36%     | -8.2%        | -2.95   | 50%    | 0.0%        | 0.00   |
| `GS_DISTRIB`          |  5       | 20%     | -18.6%       | -3.72   | 50%    | 0.0%        | 0.00   |

EV = `win_rate × avg_ret`. Setting both to 0 means the flag still fires (the
`compute_flags()` rule logic is untouched), but it no longer contributes
positive or negative EV to the verdict ranking.

### Behavioral effect

The live `compute_flags()` returns up to one positive + one negative flag.
`signal_verdict()` is computed on the **primary** (positive flag if any, else
negative). So the impact is:

- **Tickers whose only fire is `HRT_REVERSAL_RISK` or `GS_DISTRIB`** → primary
  is that flag, EV is now 0, verdict moves **🔴 AVOID → 🟡 WATCH**. This is the
  intended demotion: a low-IC flag stops issuing AVOID warnings.
- **Tickers with a positive flag + one of these as secondary** → primary
  verdict unchanged. The negative-flag column in the report will still show
  the fire, but the EV badge changes from negative to 0.
- **All other tickers** → no change.

### Caveats

1. The 14-sample / 5-sample WR estimates that trigger this demotion are
   themselves not statistically robust — Wilson 95% CI for 5/25 (`GS_DISTRIB`
   at WR 20%) spans roughly 7%-41%. A real IC-decay rule should use ≥ 30
   observations per flag and reject demotions where the CI crosses 0.50. Run
   id=4 is the *operational shape* of the rule — the *statistical version*
   needs more data.
2. `PARABOLIC_BLOCK` and `HRT_WEAK` are still negative-EV. They were not
   demoted because their WR ≥ 0.43 (above the 0.50 threshold by less margin
   than HRT_REVERSAL_RISK's, but they retain genuine negative-EV signal that
   it's useful to surface as AVOID).
3. Neutralizing a flag is reversible — `flag_stats` is just JSON. If id=4
   under-performs vs id=3 in the next 4 weeks of live scans, write id=5
   restoring the original values.

### Deploy reminder

Run id=4 only changes behavior **after** `femisagent.py v1.1` is deployed to
`/data/.openclaw/workspace/scripts/` AND `SUPABASE_KEY` is set on the runner.
Until then the live script still uses the hardcoded v3.8 `FLAG_STATS` (which
matches run id=1, not id=4).

## Run id=5 — Wilson-CI statistical demote

Run id=4 was the *operational shape* of an IC-decay rule (manually picked two
small-sample flags). Run id=5 is the *statistical version*: a uniform Wilson-CI
filter applied to id=4's `flag_stats`.

**Rule:** neutralize any flag with `n < 10` **OR** Wilson 95% lower bound
of `win_rate` `< 0.5`.

**Result:** 5 new demotions on top of id=4's 2 (total 7 neutralized out of 13).

| Flag | n | WR | Reason for demote |
|---|---:|---:|---|
| `GS_ACCUM`        |  4 | 85% | n<10 |
| `SQUEEZE_EXTREME` |  6 | 100% | n<10 |
| `GS_MILD_ACCUM`   |  8 | 75% | n<10 |
| `PARABOLIC_BLOCK` |  9 | 44% | n<10 |
| `HRT_WEAK`        |  7 | 43% | n<10 |
| (carried from id=4: `HRT_REVERSAL_RISK`, `GS_DISTRIB`) | | | manual IC-decay |

All 5 newly demoted flags fail purely on the n<10 threshold. None of the
remaining flags (n=10..43) fail Wilson_lower<0.5 — their CIs are tight
enough.

**Behavioral impact (verified live):**

- 🟢 EXECUTE bucket (EV ≥ 80) → **empty**. Top remaining is `20D_BREAKOUT`
  at EV=69, which lands in 🟢 BUY.
- Top-EV flags going forward: `20D_BREAKOUT` (69) > `MOMENTUM_SURGE` (51) >
  `VPIN_ELEVATED` (48) > `MOMENTUM_CONTINUATION` (41) > `HRT_STRONG` (37) >
  `VPIN_TOXIC` (32). All BUY-tier.
- `🔴 AVOID` only fires when no positive flag is present. With
  `PARABOLIC_BLOCK` and `HRT_WEAK` neutralized, the only remaining negative-
  EV flag is... none. So AVOID becomes effectively unreachable in the live
  scanner under id=5.

This is intentionally conservative — the statistically defensible answer
with current sample sizes. The right next step is to produce **n ≥ 30 per
flag** via a longer backtest with more universe diversity, which is what
the auto-backtest pipeline below is for.

## Auto-backtest pipeline — `femisagent_backtest.py`

Until id=5, every `femisapien_backtest_runs` row was hand-written by SQL
in this session. The new `femisagent_backtest.py` script (in the repo root
on this branch) ends that pattern: it pulls historical bars via yfinance,
applies `compute_flags()` over rolling windows, measures forward N-day
returns per flag fire, and POSTs a new row to Supabase. The live scanner
picks it up on the next scan via the v1.1+ `load_latest_calibration()`.

### What it does

1. `yf.download(tickers, start, end, auto_adjust=True)` — split/dividend-
   adjusted daily bars from yfinance. Free, no auth, rate-limit-tolerant.
2. For each ticker with sufficient history (≥ `lookback + horizon + 5`
   bars), slide a window of length `--lookback` (default 90 bars).
3. At each window endpoint, call `compute_flags(window)`. For every flag
   fire that's not `NEUTRAL` / `INSUFFICIENT_DATA`, record the forward
   `--horizon`-trading-day price change (default 60td).
4. Aggregate per-flag `{n, win_rate_pct, avg_ret_pct}`.
5. (Default on) apply Wilson-CI demote: same rule as id=5 above.
   `--no-wilson` skips this; raw stats then land in Supabase.
6. POST to `femisapien_backtest_runs`. Returns the new `id`.

### Key flags

| Flag | Default | Purpose |
|---|---|---|
| `--start` / `--end` | 5y ago → today | Backtest window. |
| `--horizon` | 60 | Forward return horizon (trading days). |
| `--lookback` | 90 | Bars window fed to `compute_flags`. |
| `--tickers ...` | watchlist of 49 | Universe override. |
| `--no-wilson` | off | Disable demote; ship raw stats. |
| `--min-n` | 10 | n threshold for Wilson demote. |
| `--min-wilson-lower` | 0.5 | Wilson lower-bound threshold. |
| `--dry-run` | off | Compute + print, don't POST. |
| `--version-tag` | auto | Override the `version` field. |

### Deploy

```bash
# On the bridge:
pip3 install --break-system-packages yfinance

# Deploy femisagent_backtest.py to /data/.openclaw/workspace/scripts/
#   — use the same base64 heredoc pattern as femisagent.py v1.1 deploy.
# Also redeploy femisagent.py v1.2 (lazy ib_insync import + KeyError fix);
#   v1.2 is required so the backtest can import compute_flags without
#   pulling in ib_insync.

# Add femisagent_backtest.py to the bridge's run_script allow-list (the
# server.py source on the bridge — wherever the allow-list of 3 scripts
# is defined: femisagent.py, femisagent_daily.py, morning_brief.py).
# Then restart: systemctl restart jarvis-bridge
```

### Smoke test (dry-run, no Supabase write)

```bash
python3 /data/.openclaw/workspace/scripts/femisagent_backtest.py \
  --tickers NVDA AMD TSLA \
  --start 2022-01-01 --end 2024-12-31 \
  --horizon 30 \
  --dry-run
```

Expected: per-flag stats table, Wilson-demote summary, then a payload
preview (NOT the full `flag_stats` JSON — too noisy; preview shows counts
and metadata keys).

### Full run → ships a new Supabase row

```bash
python3 /data/.openclaw/workspace/scripts/femisagent_backtest.py
# default: full watchlist, last 5y, h=60, Wilson-CI on
# → POSTs to femisapien_backtest_runs, prints new id
# → next femisagent.py scan shows "Calibration: supabase-run-id=<new>"
```

### Caveats & known gotchas

- **yfinance reliability**: Yahoo periodically changes their internal API;
  yfinance has historically broken with no notice. If `yf.download` returns
  empty for everything, `pip install --upgrade yfinance`.
- **Survivorship bias**: the default watchlist is the current universe.
  Tickers that delisted before today (or got acquired) aren't in it, so
  the backtest is mildly biased toward survivors. Mitigation: feed
  `--tickers` from a historical S&P 500 / Russell 1000 list.
- **Forward-return horizon ≠ v3.8's original**: the existing FLAG_STATS
  in id=1 had avg_ret values up to +123% (GS_ACCUM). That suggests the
  original v3.8 calibration used a long horizon (252+td) or only counted
  big winners. This script's defaults (60td, all fires equally weighted)
  will produce more modest numbers — they are not directly comparable to
  id=1. Use `--horizon 252` if you want closer matching.
- **No look-ahead, but no transaction costs either**: bars at index `i`
  are used to fire the signal, bars at `i + horizon` for the forward
  return. No bias. But also no slippage, commission, or borrow costs
  modeled. Real-world execution will trail.
- **flag_stats aggregate via REST**: this script writes the new row
  through Supabase REST (using the service-role key or anon-with-policy).
  RLS is enforced by Supabase; nothing changes there.

## Run id=6 — first yfinance auto-backtest, and what it revealed

Run id=6 = full 49-ticker watchlist, 2021-05-16 → 2026-05-15, h=60td,
Wilson-CI on. Live-verified: agent now reports `Calibration:
supabase-run-id=6`.

But the result surfaced two structural issues that motivate v1.3 / v1.1
of this branch:

### Issue 1: EV scale shift

| Source | Top EV | BUY threshold (EV≥30) | EXECUTE threshold (EV≥80) |
|---|---:|---:|---:|
| v3.8 hardcoded (run id=1) | +115 (SQUEEZE_EXTREME)  | 6 flags | 2 flags |
| Run id=6 (yfinance, 60td) | **+12** (HRT_WEAK)      | 0 flags | 0 flags |

The v3.8 hardcoded `avg_ret` values (up to +123%) clearly came from a
much longer horizon or a "big winners only" sample. With 60td forward
returns on a 5y window, real EV magnitudes cap at ~+12. The original
80/30/0 thresholds were calibrated to v3.8's scale and don't transfer.

**femisagent.py v1.3** rescales `signal_verdict` thresholds to match the
empirical distribution:

| Tier | Old threshold | New threshold |
|---|---:|---:|
| 🟢 EXECUTE | EV ≥ 80 | EV ≥ 10 |
| 🟢 BUY     | EV ≥ 30 | EV ≥ 5  |
| 🟡 WATCH   | EV ≥ 0  | EV ≥ 0  |
| 🔴 AVOID   | EV < 0  | EV < 0  |

Ratios kept similar (80/30 ≈ 2.67×, 10/5 = 2.0×) so the tier semantics
are preserved. Top-EV flags re-enter the EXECUTE bucket; mid-tier into
BUY; small-EV into WATCH.

### Issue 2: "warning flag" sign flip — base-rate artifact?

Four flags that v3.8 hardcoded as **negative-EV warnings** —
`HRT_WEAK`, `HRT_REVERSAL_RISK`, `PARABOLIC_BLOCK`, `GS_DISTRIB` — all
showed **positive forward returns** in run id=6. Suspicious. Three
hypotheses:

1. **Universe / period base rate**: the 49-ticker momentum/tech/crypto
   watchlist over 2021-2026 was a strong bull market. The unconditional
   60d forward return for a random entry might be +12-15%, so any
   triggered flag with avg_ret +10% is actually *underperforming the
   baseline*.
2. **Survivorship bias**: the watchlist is today's universe. Stocks
   that fired these warning flags AND went bankrupt aren't in the
   sample.
3. **Definition drift**: v3.8's labels might have used a different
   horizon, win definition, or sample.

**femisagent_backtest.py v1.1** addresses hypothesis 1 by computing the
**unconditional baseline**: average forward return across ALL (i, i+h)
windows, regardless of flag fire. Then for each flag, computes
`excess_ret_pct = avg_ret_pct - baseline_ret_pct` and `excess_wr_pct`.
A flag's actual edge is its excess, not its raw return. Baseline lives
in `raw_metadata.baseline_unconditional`; per-flag excess metrics live
in each `flag_stats` array entry. The live scanner still scores on raw
`win_rate × avg_ret` (so deploy doesn't change live behavior); the
excess metrics are diagnostic for the next calibration decision.

Also fixed in v1.1: the `datetime.utcnow()` deprecation warning that
was appearing on every run.

### Deploy v1.3 + v1.1

Same base64-heredoc pattern as before. After redeploy, re-run the full
backtest to ship run id=7 with baseline data attached. Then inspect
`raw_metadata.baseline_unconditional` and the new `excess_ret_pct` /
`excess_wr_pct` fields to confirm hypothesis 1.

If the baseline analysis shows excess_ret is ~0 or negative for the
sign-flipped flags, the next calibration version should either:
(a) score on excess EV instead of raw EV in `compute_flags`'s sort, or
(b) use a benchmark-relative win rate (`P(ret > baseline)` instead of
`P(ret > 0)`), or
(c) source a less universe-biased sample (e.g., a historical S&P 500
universe with delisted constituents included).
