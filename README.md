> **For an existing live EDGE NSE / EdgeSwing repository:** this release archive is a selective upgrade overlay, not a fresh repository image. Extract it at the repository root and replace only the files included in the archive. It intentionally omits `data/history.csv`, `data/latest_screen.json`, `data/last_run.json`, and `data/etf_symbols.csv` so your live 260-session history, current screen and ETF cache are preserved. After committing the code files, the workflow runs on source changes and publishes the new proposal dashboard/EOD snapshot. If it does not trigger, open **Actions → Refresh EDGE NSE research screen → Run workflow**.

# EDGE NSE — Automated EOD Research Screener

A static GitHub Pages front end plus a scheduled Python ingestion and screening pipeline. The website has no build step and no browser-side external market-data calls. A GitHub Actions workflow fetches end-of-day files, validates and normalizes them, computes indicators, screens candidates, and commits the latest results for GitHub Pages to serve.

**Research only:** this project does not connect to a broker, place orders, provide intraday/live prices, or guarantee returns. The initial website demo is synthetic until the first successful Actions run.

## What is automated

1. **Historical bootstrap:** on the first run (or when stored history has fewer than 205 equity sessions), the workflow tries to backfill up to 260 recent NSE sessions.
2. **Scheduled update:** the workflow runs Monday–Friday at approximately **20:30 Asia/Kolkata**. GitHub Actions can be delayed, and an NSE archive may not yet be published when the job starts.
3. **Official cash-equity source:** downloads NSE CM-UDiFF Common Bhavcopy ZIP files and retains EQ-series rows only. Common non-EQ series are excluded.
4. **ETF exclusion:** refreshes the official NSE "Securities available for trading in ETF" CSV and removes those symbols from stored history before screening. A committed cache/curated fallback is used if NSE's ETF-list endpoint is temporarily unavailable; the run warning identifies fallback use. This prevents liquid, gold and index ETFs from being mistaken for individual company stocks.
5. **Benchmark:** separately attempts to obtain NIFTY 50 historical index OHLC through NSE's historical index endpoint. If unavailable, relative-strength signals are disabled; breakout and pullback can still be evaluated.
6. **Validation:** verifies the expected schema, parseable dates/numbers, positive OHLC prices, valid high/low envelopes and nonnegative volume. Invalid source files are rejected rather than partially accepted.
7. **History store:** deduplicates by `(date, symbol)` and retains a rolling 320-session CSV. `data/history.csv` is the normalized source history; `data/last_run.json` records ingestion statistics and warnings.
8. **Screening:** computes SMA20/50/200, EMA20, Wilder RSI(14), simple-average ATR(14), 20-session volume/turnover averages and 60-session return. It applies the rules below and publishes `data/latest_screen.json`.
9. **Website:** the Overview and Stock scanner fetch the published JSON from the same GitHub Pages origin. The Data & research panel shows the history length, benchmark date and warnings. Manual CSV uploads are still available.

## Market explorer

Five additional views (sidebar → **Market explorer**) turn the same stored NSE history into a general stock-analysis tool, without any paid data subscription:

- **Markets:** NIFTY 50, advance/decline, % of stocks above SMA50/SMA200, new 52-week highs/lows, a breadth chart, and movers (gainers, losers, volume shockers, 52W highs/lows, gap ups) filterable by size bucket and liquidity.
- **Stock analysis:** search any NSE EQ stock (topbar search or `#stock/SYMBOL` links). Daily candlestick chart with SMA20/50/200, 1D–1Y returns vs NIFTY 50, 52-week range, RSI, MACD, ATR, volume, event flags, industry peers, and a one-click hand-off to the risk calculator (entry = close + 0.25%, stop = close − 2×ATR, target = 2R).
- **Scans:** 24 prebuilt scans (52W high/low, 20-session breakout on volume, golden/death cross, MACD crossovers, RSI zones, volume shockers, gaps, engulfing, NR7, inside day, relative strength vs NIFTY) plus a custom condition builder saved in the browser.
- **Sectors:** median 1D/1W/1M/3M/1Y returns, % above SMA50 and advance/decline per NSE industry.
- **Watchlist:** browser-local list with CSV export.

`.github/scripts/build_market_data.py` builds `data/market/` (snapshot, breadth history and one chart file per stock) from `data/history.csv` during the workflow. That output is not committed. Company names, industries and large/mid/small/micro buckets come from NSE's Nifty 100, Midcap 150, Smallcap 250 and Microcap 250 constituent CSVs, cached in `data/index_constituents.csv`; other listings show as "Unclassified". The build step is non-blocking, so a failure never stops the screener from deploying.

Limits: end-of-day data only (no intraday), unadjusted prices (returns and 52-week ranges spanning a flagged ≥40% one-day move are left blank), no shareholding, delivery % or F&O data, and about 13 months of history.

### Conviction scoring, market regime and backtest evidence

Most scan libraries, StockEdge's ~510 included, are single conditions (one indicator crossing one level). `.github/scripts/conviction.py` instead scores every stock 0–7 on independent confirmations:

- uptrend (close > SMA50 > SMA200, SMA200 rising)
- beating NIFTY 50 over 55 sessions
- RSI 50–70 with MACD above signal
- close above 20-session VWAP
- ADX(14) ≥ 20
- within 15% of the 52-week high
- up-day volume above down-day volume

**High-conviction scans** combine the score with a trigger and need ₹5 cr average turnover: conviction leaders, high-conviction breakout, pullback and VWAP reclaim.

**Market regime gate:** NIFTY 50 above its 50-session SMA. In stored history this mattered more than any stock-level filter. From 2026-07-22 to 2026-11-16, every long setup lost about 3% over 10 sessions when NIFTY was below its SMA50, and only some scans beat the average stock when it was above.

**Evidence:** every high-conviction scan and every existing screener setup is replayed over stored history, and the explorer's Scans page and the Stock scanner's regime banner publish the results. The method:

- new entries only
- entry at the next session's open, exit at the close after 5 or 10 sessions
- results compared with the average eligible stock on the same dates
- split-affected stocks excluded

A scan is labelled "edge supported" only with at least 30 regime-on signals, a positive median and a win over the average stock.

Limits:
- one market cycle of data
- survivorship bias
- signals cluster on few dates, so effective sample sizes are small
- older sessions use a typical-price VWAP approximation

Treat verdicts as directional evidence, and re-check them as history accumulates. The verdicts recompute on every run.

### Fundamentals (P/E, market cap, P/B, dividend yield, ROE, ROCE, debt/equity)

Free automated sources are blocked from GitHub's cloud runners: NSE's website returns 403 behind bot protection and Yahoo Finance returns 429. So fundamentals come from a CSV you commit as `data/fundamentals.csv`, refreshed after each quarterly results season.

1. Export a screen covering the stocks you care about from Screener.in or a similar service. Include the NSE code (or ISIN, or company name) plus these columns: current price, P/E, market cap (₹ cr), book value, dividend yield, ROE, ROCE and debt to equity. Save it as CSV (not .xlsx).
2. Commit it as `data/fundamentals.csv`. The push triggers the workflow, which rebuilds and redeploys the site.

Column names are matched loosely, so "Price to Earning", "P/E" and "PE" all work; see `ALIASES` in `.github/scripts/fundamentals.py`. You can also use the plain format in `data/fundamentals.template.csv` (`symbol, as_of, eps_ttm, shares_cr, book_value, dps, roe, roce, debt_to_equity`). Rows match on NSE code first, then ISIN, then company name, using NSE's equity list cached in `data/nse_equity_list.csv`.

How the numbers stay current: the build stores only slow-moving inputs (trailing EPS = price ÷ P/E, shares = market cap ÷ price, book value and dividend per share) and recomputes P/E, market cap, P/B and dividend yield at every daily close. ROE, ROCE and debt/equity are shown as exported. The export date comes from an `as_of` column, or else the file's last commit date.

Safeguards: loss-making companies show "Loss" instead of a negative P/E. If a stock has a ≥40% one-day price jump after the export date (usually a split or bonus), its EPS and share count are stale, so its fundamentals are blanked with a warning until you re-export. Rows that match no NSE EQ symbol are listed in the build log.

With fundamentals loaded, the explorer adds a Fundamentals panel on each stock page, P/E and market cap columns in peers, sector and watchlist tables, five fundamentals scans (value with quality, high ROCE with low debt, dividend yield ≥ 3%, below 1.5× book, uptrend at a reasonable P/E), sector median P/E and total market cap, and fundamentals fields in the custom scan builder. Without the file, these stay hidden.

Run it locally:

```bash
python .github/scripts/build_market_data.py
python -m http.server 8000
```

Then open http://localhost:8000. The page must be served over HTTP; opening `index.html` as a file blocks the data fetches.

## Setup and deployment

1. For a new repository, use the full project package and upload its contents to the root. For the current live EdgeSwing repository, use the selective upgrade archive: extract at the root and upload only the files included in that archive; do not replace the existing `data/` files.
2. In **Settings → Pages**, set **Source** to **GitHub Actions**. This matters: commits made by `GITHUB_TOKEN` do not themselves trigger a branch-based Pages rebuild, so this workflow publishes a Pages artifact directly after refreshing the JSON.
3. The workflow already requests `contents: write`, `pages: write` and `id-token: write`. Ensure repository Actions permissions and Pages deployment policy allow these permissions.
4. Open **Actions → Refresh EDGE NSE research screen → Run workflow** to start the initial backfill immediately. The first run automatically bootstraps history when it detects fewer than 205 equity sessions. Use `force_backfill` only to rebuild the lookback or recover missing recent history.
5. Watch the Actions run and the **github-pages** environment deployment. The workflow commits `data/history.csv`, `data/latest_screen.json`, `data/last_run.json` and the refreshed `data/etf_symbols.csv` cache, then deploys a minimal static artifact containing the website and latest shortlist. Future weekday runs update it automatically.

If the first run reports `source_unavailable`, inspect the workflow log and `data/last_run.json`, then rerun later. NSE can return transient access errors or delay report availability. The first backfill can take several minutes because it checks daily archives. Scheduled workflows run from the repository's default branch, and GitHub may delay scheduled starts. Keep the repository active and periodically check that Actions remains enabled. Benchmark history is refreshed when it is missing, shorter than 61 sessions, or behind the latest equity session.

## Research rules

The automated screen mirrors the existing browser logic:

- **Liquidity floor:** 20-session average close × traded volume must be at least ₹1 crore.
- **Common trend gate:** close > SMA50 > SMA200.
- **Breakout:** close above the prior 20-session high, relative volume ≥ 1.5×, RSI(14) from 52 through 72, and the common trend gate.
- **Pullback:** common trend gate, close above EMA20 within 2.5%, previous close at or below previous EMA20, and RSI(14) from 42 through 62.
- **Relative strength:** 60-session return beats NIFTY 50, the common trend gate holds, and close is above the prior 20-session high. This setup requires a sufficiently complete benchmark series.
- **Edge score:** weighted heuristic using trend, relative volume, RSI and momentum (25% / 20% / 25% / 30%). It is a ranking score, **not** a probability of profit.
- **VWAP (informational, opt-in filter):** daily VWAP = NSE bhavcopy traded value (`TtlTrfVal`) ÷ volume. The 20-session VWAP = sum of traded value ÷ sum of volume over the last 20 sessions, and is blank if any session in the window lacks traded value. A traded value whose implied VWAP falls outside the day's low–high range is discarded. VWAP does not change which setups qualify. The scanner's **VWAP confirmation** filter (default off) can narrow the shortlist to closes above today's VWAP, or above both VWAPs. This is end-of-day VWAP, not the intraday VWAP used for entries during the session. On the first run after this change, the workflow re-downloads up to 25 recent bhavcopies once to add traded value; older stored sessions remain without it.
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
- `data/history.csv`: normalized rolling OHLCV history with listed ETFs excluded (populated by Actions)
- `data/etf_symbols.csv`: last successfully downloaded official NSE ETF symbol list, or the curated fallback list until refreshed
- `data/latest_screen.json`: published website data and qualified candidate list
- `data/last_run.json`: ingestion log and warnings
- `tests/test_nse_pipeline.py`: offline parser/indicator tests, run with `python -m unittest discover -s tests -v`

## Safety

This is a research utility, not investment advice. There is no live broker integration, secret storage, order placement, or automatic trade execution. Do not trade from synthetic demo data, stale output, unadjusted-price history, or an unvalidated heuristic score.

## Trade proposals and end-of-day paper tracking

The Stock scanner now turns each screened candidate into a **research proposal** using the capital/risk profile saved in the current browser. The proposal view shows:

- **Setup quality tier:** High (score 70–100), Medium (50–69), or Low (0–49). These bands describe only the existing heuristic score; they are not win probabilities.
- **Entry trigger:** a paper stop-entry trigger set 0.25% above the latest completed signal close, so the proposal is not assumed filled at the same close used to generate it.
- **Initial stop:** the candidate ATR-based stop estimate (`close − 2 × ATR(14)`).
- **Targets:** target 1 at 1R and target 2 at 2R from the planned entry. The tracker recalculates target rails if the entry fills after a gap.
- **Position size:** integer share quantity based on the risk-per-proposal budget, remaining 1.5% aggregate planned/open risk cap, and 15% capital notional cap. The dashboard also estimates round-trip costs from the user-supplied percentage.
- **Time rules:** entry trigger valid for three sessions; after entry, the default max hold is ten sessions. The user can select a 2, 5, 7 or 10 session max holding window.

### EOD tracking logic

`latest_screen.json` includes `recent_market_dates` and compact `recent_eod_bars` for the latest 12 equity sessions. The browser uses these bars to reconcile local proposals on each visit, even if the page was not opened every day:

- Entry activates if a daily high reaches the entry trigger. If the open gaps above the trigger, the open is used as the hypothetical fill and quantity is reduced if required by the stored risk/notional limits.
- Stop-loss takes precedence if stop and target 2 both appear to be touched in the same daily bar, since daily OHLC cannot determine their intraday order.
- Target 2 before stop = **PASS**. Stop-loss or two consecutive EOD closes below EMA20 = **FAIL**. Reaching max holding sessions without target 2 = **TIME EXIT**. No trigger within three sessions = **EXPIRED**.
- A close-to-close move of at least 40% during a tracked proposal pauses automated outcome interpretation and requests manual review for a potential corporate action or data issue.
- If tracking falls outside the published recent-bar window, the tracker requests manual reconciliation instead of fabricating a result.

**Important browser-only limitation:** the proposal ledger and capital settings are stored in `localStorage`, so they belong to that browser/device only. GitHub Actions updates shared market data, but it cannot see the private local ledger. The page must be opened to apply updates to the local ledger; it cannot send an alert while closed, and other devices will not see the same proposals unless you export/import the tracker CSV. No order is placed; hypothetical EOD fill rules are not the same as broker fills.

After adding this release to the deployed repository, run **Actions → Refresh EDGE NSE research screen → Run workflow** once. That generates the new `recent_eod_bars` field and deploys the updated proposal dashboard. Confirm a `ready` run and nonzero recent EOD bar count before using the tracker.
