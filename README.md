# EDGE NSE — GitHub Pages Website

A standalone responsive static website for research-first Indian NSE cash-equity screening. Built with plain HTML, CSS and JavaScript; no build step or server required.

## Deploy to GitHub Pages

1. Create a new public GitHub repository, for example `edge-nse-trading`.
2. Upload all files and folders in this package to the repository root. Keep `index.html`, `styles.css`, and `app.js` at the root.
3. In GitHub, open **Settings → Pages**.
4. Under **Build and deployment**, choose **Deploy from a branch**.
5. Select branch `main` and folder `/ (root)`, then Save.
6. Wait for the Pages deployment to finish. The URL will look like `https://YOUR-USERNAME.github.io/REPOSITORY-NAME/`.

If using an existing repository, commit these files to the intended branch and ensure `index.html` is in the publishing root. This package is independent and does not modify the existing Python EDGE PROTOCOL project.

## Features

- Overview dashboard with synthetic demo prices, explicit empty-state guidance, and a release checklist.
- Browser-based single-CSV or multi-file CSV import with structural OHLCV validation and cross-file duplicate checks.
- Rule-based breakout, trend-pullback and relative-strength screening.
- Heuristic candidate ranking and CSV export.
- Position-sizing / stop-loss / reward-to-risk calculator.
- Local paper ledger stored in browser `localStorage`, with CSV export.
- Responsive mobile navigation.
- No external JavaScript dependencies or third-party data requests.

## CSV import formats

The canonical combined-file template is:

`date,symbol,open,high,low,close,volume`

The importer also recognizes common NSE cash-market bhavcopy headers, including:

- **Older format:** `SYMBOL`, `SERIES`, `OPEN`, `HIGH`, `LOW`, `CLOSE`, `TOTTRDQTY`, `TIMESTAMP`.
- **Newer format:** `TradDt`, `TckrSymb`, `SctySrs`, `OpnPric`, `HghPric`, `LwPric`, `ClsPric`, `TtlTradgVol`.

When a series column is present, only `EQ` rows are imported; other series are deliberately excluded to avoid duplicate symbol/date records. Comma-grouped numeric cells such as `"1,50,000"` are supported when correctly quoted as CSV fields. If a file has unquoted thousands separators, re-export or quote those values first because commas are also the CSV column delimiter.

Dates are normalized from `YYYY-MM-DD`, `DD-MMM-YYYY` (for example, `15-Oct-2026`), and unambiguous numeric `DD/MM/YYYY` or `DD-MM-YYYY` formats. Numeric slash- or dash-separated dates where both the day and month are 01–12 (for example, `10/05/2026` or `10-05-2026`) are rejected rather than guessed; convert them to ISO dates to prevent a silent month/day swap. Invalid calendar dates such as `31-Feb-2026` are rejected. Select several daily bhavcopy CSVs at once or drag them together onto the dropzone; the browser combines them before duplicate-date validation and screening. Every selected file must use a recognized OHLCV or NSE bhavcopy header, and the whole batch is rejected if any file/row has an import error.

The complete upload batch is rejected if any data row has invalid dates/numbers/OHLC values or if duplicate symbol/date keys remain, including duplicates across different files. The import feedback lists detected bad lines with source file names, while the current data and Data health panel remain unchanged until a valid batch is loaded. Blank rows are ignored. The data health panel flags close-to-close moves of 40% or more as possible corporate-action or data discontinuities. This is only a warning: prices are not split/bonus adjusted, so use adjusted historical data whenever possible and review every alert before interpreting the scanner output. Symbols with fewer than 200 rows are flagged because the trend filter requires SMA200. Relative-strength signals are disabled when a NIFTY50/NIFTY_50 benchmark series is missing. The scanner evaluates only each symbol’s latest available bar and ignores symbols whose last date is more than 10 calendar days behind the dataset’s latest date.

An NSE equity bhavcopy usually does **not** contain an index benchmark row. Add a separate NIFTY50/NIFTY_50 history using the standard template if you want relative-strength signals. Use properly adjusted data and a provider you are licensed to use. For reliable backtests, include delisted names and point-in-time universe membership where possible.

## Critical limitations

- The demo dataset is synthetic, not NSE market data.
- GitHub Pages cannot run the Python backtesting engine or securely store broker API credentials.
- No live prices, live broker connection, automated order routing or live trading are implemented.
- Browser scanner uses the latest available completed bar per symbol, simple-average 14-bar ATR, Wilder-smoothed RSI, and simplified indicator rules. It is a candidate discovery aid, not a validated strategy. The stop estimate intentionally uses simple-average ATR and may differ from charts using Wilder ATR.
- The equity chart on the overview is illustrative and is explicitly not a backtest.
- Data validation checks structure and warns about close-to-close changes of at least 40%, but it cannot verify source authenticity, determine whether a move is a corporate action, adjust prices for splits/bonuses, or verify survivorship bias. Multi-file upload makes it easier to combine daily bars but does not make unadjusted bhavcopy history safe for research.
- Paper ledger and readiness checklist are stored only in the current browser and are not synced.
- A checked release checklist is a personal note, not authorization or proof of readiness.
- For rigorous backtesting, use the separate Python project, calibrated transaction costs, walk-forward validation and forward paper trading.

## Safety

This is a research tool, not investment advice. It does not promise profits. Do not use synthetic values or heuristic scores to place real trades.
