# EDGE NSE — Automated EOD Research Screener

A static GitHub Pages front end plus a scheduled Python ingestion and screening pipeline. The website has no build step and no browser-side external market-data calls. A GitHub Actions workflow fetches end-of-day files, validates and normalizes them, computes indicators, screens candidates, and commits the latest results for GitHub Pages to serve.

**Research only:** this project does not connect to a broker, place orders, provide intraday/live prices, or guarantee returns. The initial website demo is synthetic until the first successful Actions run.

## What is automated

1. **Historical bootstrap:** on the first run (or when stored history has fewer than 205 equity sessions), the workflow tries to backfill up to 260 recent NSE sessions.
2. **Scheduled update:** the workflow runs Monday–Friday at approximately **20:30 Asia/Kolkata**. GitHub Actions can be delayed, and an NSE archive may not yet be published when the job starts.
3. **Official cash-equity source:** downloads NSE CM-UDiFF Common Bhavcopy ZIP files and retains EQ-series rows only. Common non-EQ series are excluded.
4. **Benchmark:** separately attempts to obtain NIFTY 50 historical index OHLC through NSE's historical index endpoint. If unavailable, relative-strength signals are disabled; breakout and pullback can still be evaluated.
5. **Validation:** verifies the expected schema, parseable dates/numbers, positive OHLC prices, valid high/low envelopes and nonnegative volume. Invalid source files are rejected rather than partially accepted.
6. **History store:** deduplicates by `(date, symbol)` and retains a rolling 320-session CSV. `data/history.csv` is the normalized source history; `data/last_run.json` records ingestion statistics and warnings.
7. **Screening:** computes SMA20/50/200, EMA20, Wilder RSI(14), simple-average ATR(14), 20-session volume/turnover averages and 60-session return. It applies the rules below and publishes `data/latest_screen.json`.
8. **Website:** the Overview and Stock scanner fetch the published JSON from the same GitHub Pages origin. The Data & research panel shows the history length, benchmark date and warnings. Manual CSV uploads are still available.

## Setup and deployment

1. Upload **the contents** of this package to the root of the intended GitHub repository. The archive includes `index.html`, `app.js`, `styles.css`, `.github/workflows/refresh-edge-nse.yml`, `.github/scripts/update_nse_data.py` and the `data/` folder.
2. In **Settings → Pages**, set **Source** to **GitHub Actions**. This matters: commits made by `GITHUB_TOKEN` do not themselves trigger a branch-based Pages rebuild, so this workflow publishes a Pages artifact directly after refreshing the JSON.
3. The workflow already requests `contents: write`, `pages: write` and `id-token: write`. Ensure repository Actions permissions and Pages deployment policy allow these permissions.
4. Open **Actions → Refresh EDGE NSE research screen → Run workflow** to start the initial backfill immediately. The first run automatically bootstraps history when it detects fewer than 205 equity sessions. Use `force_backfill` only to rebuild the lookback or recover missing recent history.
5. Watch the Actions run and the **github-pages** environment deployment. The workflow commits `data/history.csv`, `data/latest_screen.json` and `data/last_run.json`, then deploys a minimal static artifact containing the website and latest shortlist. Future weekday runs update it automatically.

If the first run reports `source_unavailable`, inspect the workflow log and `data/last_run.json`, then rerun later. NSE can return transient access errors or delay report availability. The first backfill can take several minutes because it checks daily archives. Scheduled workflows run from the repository's default branch, and GitHub may delay scheduled starts. Keep the repository active and periodically check that Actions remains enabled. Benchmark history is refreshed when it is missing, shorter than 61 sessions, or behind the latest equity session.

## Research rules

The automated screen mirrors the existing browser logic:

- **Liquidity floor:** 20-session average close × traded volume must be at least ₹1 crore.
- **Common trend gate:** close > SMA50 > SMA200.
- **Breakout:** close above the prior 20-session high, relative volume ≥ 1.5×, RSI(14) from 52 through 72, and the common trend gate.
- **Pullback:** common trend gate, close above EMA20 within 2.5%, previous close at or below previous EMA20, and RSI(14) from 42 through 62.
- **Relative strength:** 60-session return beats NIFTY 50, the common trend gate holds, and close is above the prior 20-session high. This setup requires a sufficiently complete benchmark series.
- **Edge score:** weighted heuristic using trend, relative volume, RSI and momentum (25% / 20% / 25% / 30%). It is a ranking score, **not** a probability of profit.
- **Stop estimate:** close − 2 × simple-average ATR(14). This intentionally may differ from a broker chart using Wilder ATR.
- **Freshness gate:** the overall screen is not marked ready with fewer than 200 equity sessions or if the latest equity date is more than 3 calendar days old (or in the future). Candidate rows are suppressed unless status is `ready`. In addition, automated candidate rows must be from the latest equity session in the stored data; older per-symbol bars are never shown as current setups.

## Data-quality caveats

- **Corporate actions:** raw bhavcopy prices can be unadjusted for splits/bonus issues. A symbol with a one-day close move of at least 40% in stored history is conservatively excluded from candidates and listed as a warning. This is not an automatic adjustment; use split/bonus-adjusted data before treating results as research evidence. Very large genuine moves can also trigger this rule.
- **NIFTY history:** if the NSE index endpoint is blocked or changes, the previous local benchmark may be stale or missing. Read `benchmark_latest_date` and the warnings; relative strength must not be interpreted as available unless benchmark data is current.
- **Universe bias:** the pipeline screens symbols found in the current stored EQ-series bhavcopies. It does not currently preserve point-in-time index membership, delisted symbols, or historical series/classification changes. Survivorship bias can materially distort performance research.
- **Source access and licensing:** the pipeline uses public NSE report URLs. Availability, access controls and data-use permissions can change. Verify NSE terms for your intended personal or commercial use and do not bypass access controls.
- **EOD lag:** this is a previous-session end-of-day research shortlist, not an intraday scanner. Public reports may be late, network restrictions may interrupt the job, and a successful workflow run does not guarantee every input source was available.
- **No independent backtest:** rule screening is not a performance test. Before any real-money decision, run walk-forward and out-of-sample backtests with costs, slippage, corporate-action-adjusted data and realistic fills. Paper trade and reconcile signals before making any readiness claim.

## Manual CSV import

The browser importer still accepts the standard template `date,symbol,open,high,low,close,volume` as well as common older/newer NSE bhavcopy headers. You can select multiple daily CSVs and merge them locally. If your upload succeeds, it switches the current page to the manual dataset; reload the page to load the latest automated output again.

Accepted dates include `YYYY-MM-DD`, `DD-MMM-YYYY` and unambiguous numeric day/month formats. Ambiguous dates such as `10/05/2026` are rejected rather than guessed. Where a Series column exists, only `EQ` rows are imported. Comma-grouped numbers must be correctly quoted in CSV fields.

## Files

- `index.html`, `app.js`, `styles.css`: static website
- `.github/workflows/refresh-edge-nse.yml`: scheduled and manual trigger
- `.github/scripts/update_nse_data.py`: source ingestion, validation, indicators, screen and JSON generation
- `data/history.csv`: normalized rolling OHLCV history (populated by Actions)
- `data/latest_screen.json`: published website data and qualified candidate list
- `data/last_run.json`: ingestion log and warnings
- `tests/test_nse_pipeline.py`: offline parser/indicator tests, run with `python -m unittest discover -s tests -v`

## Safety

This is a research utility, not investment advice. There is no live broker integration, secret storage, order placement, or automatic trade execution. Do not trade from synthetic demo data, stale output, unadjusted-price history, or an unvalidated heuristic score.
