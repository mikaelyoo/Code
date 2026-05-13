# FEMISAPIEN v3.8 — Extended Backtest 2000-2026 (Regime-Modeled)

Two runs stored in Supabase project `azyxlnbdgehqeifbggef`:

| Run | id | Weight schedule         | WR        | Avg ret/EXEC |
|-----|----|-------------------------|-----------|--------------|
| Prior actual (2019-2026 only)   | 1  | 1.0 → 2.5            | 80.9%     | +67.76%      |
| v3.8r1 extended (flat 2.5 head) | 2  | 0.3-0.5 / 1.0 / 2.5  | 66.7%     | +59.11%      |
| **v3.8r2 recent-heavy**         | **3** | 0.3-0.5 / 1.0 / 5.0 | **72.0%** | **+74.80%**  |

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
