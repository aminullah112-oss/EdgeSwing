# ARR Market Intelligence — deployment (v0.5)

Frontend: `frontend/`, published by the EDGE NSE Pages workflow at `/EdgeSwing/arr-market-intelligence/`.
Backend: FastAPI on a separate always-on host (`../render.yaml`). Single user only: Zerodha data must not be redistributed.

## Backend on Render
1. New > Blueprint > select this repository. Enter `KITE_API_KEY` and `KITE_API_SECRET` when asked. `APP_ACCESS_KEY` is generated.
2. In the Kite developer console set the redirect URL to `https://<service>.onrender.com/auth/callback`.
3. Open the dashboard > Data setup. Enter the backend URL and the `APP_ACCESS_KEY` value, then Connect Zerodha.
4. Sync instruments, Load universe, Run full daily scan.

Zerodha sessions expire daily and are held in memory, so connect again each trading day (and after any restart). The 15:50 IST scan runs only while connected.

## Changes in v0.5
- Bearer-key protection on `/api/*` and `/ws/live`, one-time state on the Zerodha callback, restricted CORS, API docs disabled. `GET /auth/login` is replaced by `POST /api/auth/login-url`.
- Relative strength compares matching dates and returns nothing if a benchmark is stale.
- Supertrend now uses ATR(10), multiplier 3, as documented (code previously used ATR 14).
- VWAP (session on intraday, rolling 20 on daily+) and an EMA20 pullback flag added.
- Backtest charges default to 11.5 bps per side (was 3 bps).
- Kite history requests use IST regardless of host timezone; interrupted jobs no longer block later syncs; screener results are cached.
- `data/nse500.csv` is a snapshot of this repository's cached Nifty 100 / Midcap 150 / Smallcap 250 lists (501 rows). Refresh it after index rebalances.

---

# ARR Market Intelligence

Self-hosted NSE research and technical-analysis terminal using Zerodha Kite Connect. Includes a shared market-data pipeline, Python indicator engine, SQLite candle cache, technical screener, opportunity intelligence, strategy backtesting, signal tracking and dark web dashboard.

## Included analysis
- Comparative relative strength vs Nifty 50 and Nifty 500 (21/63/126/252 sessions)
- Cross-sectional RS percentile across the imported universe (not an official IBD rating)
- RSI(14), Supertrend(10,3), EMA 9/20/21/50/100/200, SMA 20/50/200
- MACD(12,26,9), ADX(14), ATR(14), Bollinger Bands(20,2), volume ratio
- Transparent 100-point heuristic strength score
- Daily, weekly and monthly analysis (weekly/monthly resampled from daily); intraday 1/3/5/10/15/30/60-minute candle storage and analysis

## Run locally

```bash
python -m venv .venv
# Windows: .venv\\Scripts\\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # Windows: Copy-Item .env.example .env
# Edit .env locally; never commit it.
uvicorn backend.app:app --reload
```
Open http://127.0.0.1:8000

## Zerodha setup
1. Configure a Kite Connect app and register `http://127.0.0.1:8000/auth/callback` as its redirect URL.
2. Set `KITE_API_KEY` and `KITE_API_SECRET` in local `.env`.
3. Start the app and click Connect Zerodha. Login and allow the server-side callback to exchange `request_token` for `access_token`.
4. The token is kept in backend memory for this process; log in again after restarting. Never share API secrets/access tokens or put them in frontend code.

## Load NSE 500
Place a current constituents CSV at `data/nse500.csv` with a `symbol` column and optional `company` column. See `data/nse500.csv.example`. Sync the Kite NSE instrument master, then load the universe. The project intentionally does not bundle a potentially stale constituents list.

## First data sync
Use Data setup. The dashboard sync button requests the first 20 universe symbols for 400 daily days. The API also supports `POST /api/history/sync` with `{"symbols":["RELIANCE","INFY"],"interval":"day","days":400}`. Expand in batches and throttle requests. Intraday history for all 500 stocks should be scheduled selectively, not fired as a single burst. Subscribe to live quotes only for a selected watchlist; Kite WebSocket has documented instrument/connection limits.

## API
`GET /api/status`, `POST /api/instruments/sync`, `POST /api/universe/load`, `POST /api/history/sync`, `GET /api/screener?timeframe=day`, `GET /api/stock/RELIANCE?timeframe=day`, `POST /api/live/subscribe`, `GET /api/live/snapshot`, `POST /api/jobs/full-sync`, `GET /api/jobs/status`, `GET /api/market/breadth`, `GET /api/reports/history`, `GET /api/reports/{id}`.

## Tests
`pytest -q`

## Known MVP gaps
- No automated official NSE 500 constituent downloader, persistent user watchlists or order execution. The EOD scheduler is process-local and requires an always-on running server.
- Live WebSocket quotes are available for selected subscribed symbols. The current live feed does not yet aggregate ticks into live intraday candles or recalculate technical signals tick-by-tick.
- Comparative RS needs adequate benchmark candles cached under symbols `NIFTY 50` and `NIFTY 500`; confirm the index instrument mapping in your Kite instrument master and seed the index history. Current universe loader maps NSE cash EQ stocks only.
- Indicator definitions can vary between vendors; compare outputs against a trusted charting source and tune initialization/rounding before using signals.

Research software only. Not financial advice. No order placement is implemented. Use data only within Zerodha and exchange terms; do not redistribute market data or expose the backend publicly without authentication and TLS.


## Daily ranked opportunity report (new)

Open **Daily report** in the dashboard or call `GET /api/daily-report`. It generates five ranked lists from the latest cached daily candles: relative-strength leaders, momentum leaders, bullish Supertrend, volume breakouts, and combined bullish confirmations. It also reports whether both benchmark series are available. Export is JSON. The same report pipeline is also invoked by the scheduled EOD scan after successful data refresh.

RS rating is a cross-sectional percentile blend of 6-month relative performance vs the available Nifty 50 and Nifty 500 series. It is not an official StockEdge/IBD rating. The screener uses cached data only and will not fabricate missing values.

## Integrated data pipeline and scheduled EOD scanner (v0.2)
- A single throttled daily pipeline refreshes Nifty 50, Nifty 500 and every imported universe constituent, then generates and archives a dated report.
- Start it manually from **Data setup → Run full daily scan**, or call `POST /api/jobs/full-sync`.
- Job progress and failures are persisted in SQLite and exposed through `GET /api/jobs/status`.
- The in-process scheduler (standard-library background thread) runs the same refresh at **15:50 Asia/Kolkata, Monday–Friday**. The app must remain running, and the Kite session must still be valid. It does not yet use an NSE exchange-holiday calendar. Zerodha sessions commonly require a fresh login; if the scheduled run is not authenticated, log in and run the scan manually.
- Report snapshots are available from `GET /api/reports/history` and `GET /api/reports/{id}`.
- NSE 500 market breadth is available from `GET /api/market/breadth` (above EMA20/EMA50/SMA200, bullish Supertrend and 21-session advancers/decliners).
- Full-universe historical requests are processed sequentially with a delay; expected run time depends on universe size and Zerodha API response times. Errors are recorded per symbol and do not stop the rest of the scan.

### Operational boundaries
The scheduler runs inside this Python process; it is not a managed cloud scheduler and stops when the server stops. For reliable unattended production use, deploy on an always-on host and move the job runner to a separate worker/queue. Live WebSocket ticks remain selected-watchlist quotes; the full-universe EOD pipeline uses daily historical candles. Intraday history is still best synced for selected symbols/watchlists rather than all 500 simultaneously. No orders are placed.

## Opportunity Intelligence Engine (v0.3)

The dashboard now includes **Opportunity Intelligence** and **Backtest & signals** views.

### Multi-timeframe confirmation
- Daily and weekly trend structure are both required for the confirmed-opportunity list.
- Cached intraday data is included as an additional confirmation layer (15-minute preferred, then 5-minute, 60-minute, 30-minute, 10-minute, 3-minute and 1-minute).
- When intraday history is absent, the opportunity is labelled `DAILY + WEEKLY CONFIRMED`; the platform does not silently classify missing data as bearish.
- Confidence is a transparent blend: 55% technical strength score and 45% available multi-timeframe alignment score. It is a ranking heuristic, not a probability of profit.

### Market regime and sector strength
- The market-regime classifier combines Nifty 50 and Nifty 500 trend structure (close vs EMA50 and EMA200) with the percentage of analysed NSE 500 stocks above EMA50. Conflicting benchmark trends produce a mixed regime.
- Regimes are `BULLISH`, `BEARISH`, `MIXED / TRANSITIONAL` or `UNKNOWN`. It is a context filter, not a market forecast.
- Sector rankings aggregate average 21-session and 63-session returns, relative-strength rating, percentage above EMA20 and bullish Supertrend breadth.
- To enable meaningful sector rankings, add a `sector` column to `data/nse500.csv` (or `industry`). Rows without sector mapping are excluded from sector ranks. Example: `symbol,company,sector`.

### ATR reference levels
For each confirmed opportunity, the platform calculates:
- Entry trigger = max(last close, prior 20-session high + 0.1 ATR)
- Stop reference = entry trigger − 1.5 ATR
- Target reference = entry trigger + 3 ATR
- Nominal reward/risk ratio = 2:1

These are mechanical research levels. They do not account for personal risk tolerance, gaps, liquidity or guaranteed execution, and are not personalised recommendations.

### Historical backtest
`POST /api/backtest` accepts `symbol`, `initial_capital`, `risk_pct`, `slippage_bps` and `max_hold`. The current rule set requires a completed 20-session volume breakout, close above EMA50, EMA20 above EMA50, RSI 50–78 and bullish Supertrend. The simulated entry is at the next session open. Position sizing is constrained by both the per-trade risk budget and available cash. Stops and targets use 1.5 ATR and 3 ATR respectively. When both are touched in one daily candle, the simulator assumes the stop occurs first. It models daily mark-to-market equity, configurable slippage and estimated 0.03% round-trip transaction costs.

This is a **single-symbol, one-position-at-a-time research backtest**. It does not yet model a multi-stock portfolio, benchmark-relative returns, exact broker/exchange charges, market impact, liquidity, circuit limits, corporate actions or queue priority. Review the assumptions returned with every result. Historical backtest performance does not predict future returns.

### Signal tracking
- Daily reports store confirmed opportunity snapshots with their signal date, trigger, stop, target and confidence.
- A tracked setup becomes a simulated position only after its entry trigger is crossed. Opening gaps beyond the target are marked `gap_missed`; setups that never trigger within 20 sessions are marked `not_triggered`.
- Once entered, the tracker records target, stop or 20-session time exits. When stop and target are both touched in one daily candle, stop is assumed first.
- `GET /api/signals/history` returns signal history; `GET /api/signals/performance` returns aggregate outcomes; `POST /api/signals/refresh` updates tracking against cached daily candles.

### Added API endpoints
- `GET /api/intelligence/summary?limit=100` (combined endpoint used by the dashboard)
- `GET /api/intelligence/opportunities?limit=100`
- `GET /api/intelligence/sectors`
- `POST /api/backtest`
- `GET /api/signals/history?limit=100`
- `GET /api/signals/performance`
- `POST /api/signals/refresh`

### Remaining operating requirements
- Zerodha credentials and sessions are not included. Configure `.env` locally and authenticate through Kite.
- Supply a current NSE 500 constituents file; no stale constituents file is bundled.
- Sector analysis is only as complete as the sector metadata provided in that CSV.
- Intraday confirmation coverage depends on live tick subscriptions and enough intraday bars being available. Daily and weekly historical analysis do not require all-universe intraday subscriptions; subscribed symbols additionally use their current tick-derived daily candle for live daily/weekly context.
- The backtester is a first research implementation and needs broader out-of-sample and portfolio validation before being relied upon. No order placement is implemented.

## Live Market-Data Engine (v0.4)

### Tick ingestion and candle construction
- The selected watchlist is subscribed through `KiteTicker` in `full` mode. Tick callbacks are processed on the KiteTicker background thread, separate from FastAPI request handling. Zerodha’s Python client provides `on_ticks`, `on_connect`, `on_close`, `on_error`, `on_reconnect` and `on_noreconnect` callbacks; the app uses these to maintain connection status and resubscribe after reconnection.
- Tick prices are aggregated into exchange-time 1-minute OHLCV bars. Zerodha's `volume_traded` field is a session-cumulative counter, so the engine converts it to minute deltas. On the first tick seen for a symbol/session, `last_traded_quantity` seeds the volume estimate; subsequent ticks use cumulative-volume differences.
- Completed 1-minute bars are stored in SQLite. The currently forming 1-minute bar remains in memory and is merged into live API responses. If the server is stopped in the middle of a minute, that partial bar is not guaranteed to survive a process restart.
- Live 3/5/10/15/30/60-minute bars are resampled from 1-minute data using a 09:15 Asia/Kolkata session anchor, and merged with any cached historical candles for that interval.
- Intraday indicators are recalculated from the latest candle set when `/api/live/analysis/{symbol}` or `/api/intelligence/live` is requested. The dashboard refreshes live metrics and the selected stock chart every five seconds; the price chart, EMA20/EMA50/Supertrend overlays, RSI panel and side metrics refresh when the chart timeframe matches the selected live interval. The browser also receives quote/status snapshots over `/ws/live`.

### Live opportunity overlay
- Subscribe a small watchlist from **Stock analysis** by entering comma-separated NSE symbols and choosing the live interval.
- The **Opportunity Intelligence → Live watchlist opportunities** table joins live intraday trend with daily and weekly trend structure. During the current session, the latest Kite full-mode daily OHLC/LTP updates the in-progress daily candle, which is also folded into the current weekly candle, so daily/weekly context can evolve before EOD. ATR reference levels are included.
- A quote must have updated within the previous 120 seconds for intraday trend to count as a live confirmation. Cached historical candles alone are never presented as a live opportunity.
- The API limits live overlay analysis to 30 watchlist symbols by default; use `GET /api/intelligence/live?interval=5minute&limit=100` for a larger analysis batch. Larger watchlists may increase API response time.
- The selected-symbol subscription list is persisted. Use **Stop feed** to close the stream and clear the persisted watchlist. After restarting the server, reconnect Zerodha and subscribe again to start a fresh stream.

### Live API endpoints
- `POST /api/live/subscribe` with `{"symbols":["RELIANCE","INFY"]}` to add symbols to the feed.
- `POST /api/live/unsubscribe` with a symbol list to remove selected subscriptions.
- `POST /api/live/restore` after reconnecting Zerodha to resume the persisted watchlist.
- `POST /api/live/stop` to stop the full stream and clear the live watchlist.
- `GET /api/live/status`, `GET /api/live/snapshot`, `GET /api/live/watchlist` for connection and quote state.
- `GET /api/live/candles/RELIANCE?interval=5minute` returns OHLCV candles with technical indicators.
- `GET /api/live/analysis/RELIANCE?interval=5minute` returns the latest intraday metrics and a freshness-gated bias label.
- `GET /api/intelligence/live?interval=5minute&limit=30` returns the live watchlist overlay.
- `WS /ws/live` streams quote snapshots, connection status and current partial candles to the dashboard.

### Runtime and validation notes
- The live tick engine requires valid Kite Connect credentials and a current access token. This package was tested with synthetic ticks and FastAPI route/WebSocket smoke tests, not against the user's Zerodha account or a live exchange session.
- The `kiteconnect` dependency is declared in `requirements.txt`; it was not installed in this build sandbox, so the external Kite connection itself could not be exercised here. Install project dependencies before starting the application.
- Live subscriptions are for selected symbols, not the entire NSE 500 automatically. Use a focused watchlist for active intraday calculations; the full universe continues to use the scheduled daily historical-data pipeline.
- Serve only over HTTPS. Access control is a single shared key (see Deployment); there are no user accounts or roles.
- Tick-to-bar aggregation does not reconstruct ticks that occurred before subscription. Seed an instrument's historical interval candles first when you need deeper intraday indicator history.
