# Changelog

## 0.3.0 — Opportunity Intelligence Engine

- Added multi-timeframe trend analysis across cached intraday, daily and weekly candles.
- Added Nifty 50 + Nifty 500 market regime classification with NSE 500 breadth confirmation.
- Added sector-strength rankings driven by the optional `sector` / `industry` field in the constituent CSV.
- Added ATR-based entry trigger, stop and target reference levels for confirmed opportunities.
- Added a single-symbol breakout backtester with next-session-open entry, risk-budget position sizing, slippage, estimated transaction costs and daily mark-to-market drawdown.
- Added persistent signal history and outcome tracking, including `not_triggered` and `gap_missed` statuses.
- Added Opportunity Intelligence and Backtest & Signals dashboard views.
- Added SQLite schema upgrades that preserve existing candle and universe data.
- Added unit and API smoke-test coverage.

## 0.4.0 — Live Market-Data Engine

- Added a thread-safe Zerodha tick-to-1-minute OHLCV aggregator using full-mode tick prices and session-cumulative traded volume.
- Persists completed 1-minute candles and keeps the currently forming minute in memory for low-latency display without a SQLite write on every tick.
- Added IST-aligned 3/5/10/15/30/60-minute aggregation from the minute stream, merged with cached historical interval candles.
- Added incremental KiteTicker subscriptions, reconnect/error state tracking, unsubscribe and stop controls.
- Added persistent selected-symbol watchlist storage.
- Added `/ws/live` for browser-side quote/status updates and `/api/live/candles`, `/api/live/analysis`, `/api/live/status` and `/api/live/watchlist` endpoints.
- Added live intraday bias metrics to Stock analysis and a live watchlist overlay to Opportunity Intelligence. The current tick-derived daily candle also updates the in-progress daily and weekly trend context.
- Added a 120-second quote-freshness gate so stale or cached bars cannot be labelled as a current live confirmation. The selected intraday stock chart refreshes candles, EMA20/EMA50/Supertrend, RSI and side metrics on the dashboard's five-second analysis refresh when chart and live intervals match.
- Added synthetic tick aggregation, volume-delta, late-tick and interval-resampling tests.
