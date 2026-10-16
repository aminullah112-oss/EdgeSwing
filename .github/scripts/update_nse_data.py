#!/usr/bin/env python3
"""Scheduled NSE EOD ingestion and rules-based EDGE NSE screen.

Uses NSE's public CM-UDiFF daily bhavcopy archive for EQ-series stocks and
NSE's historical index endpoint for the NIFTY 50 price-index benchmark.
No broker credentials, order routing, or live-order code is present.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections import defaultdict
from http.cookiejar import CookieJar
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
HISTORY_PATH = DATA_DIR / "history.csv"
SCREEN_PATH = DATA_DIR / "latest_screen.json"
RUN_PATH = DATA_DIR / "last_run.json"
HISTORY_COLUMNS = ("date", "symbol", "open", "high", "low", "close", "volume")
NSE_REPORTS = "https://www.nseindia.com/all-reports"
NSE_ARCHIVE_TEMPLATE = "https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{date}_F_0000.csv.zip"
NSE_INDEX_API = "https://www.nseindia.com/api/historical/indicesHistory"
USER_AGENT = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
MIN_AVG_TURNOVER = 10_000_000.0  # ₹1 crore, 20-session average
BACKFILL_SESSIONS = 260          # covers SMA200 plus signal/indicator warm-up
KEEP_SESSIONS = 320
TIMEOUT = 18


def iso_date(value: Any) -> str | None:
    s = str(value or "").strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%d-%b-%Y", "%d-%B-%Y", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return dt.datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            pass
    # Timestamp strings may include an ISO time suffix.
    try:
        return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return None


def num(value: Any) -> float | None:
    s = str(value if value is not None else "").strip().replace(",", "").replace("₹", "")
    if not s or s.upper() in {"NA", "N/A", "NULL", "-", "--"}:
        return None
    try:
        value = float(s)
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def clean_header(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lstrip("\ufeff").strip().lower())


def row_value(row: dict[str, str], aliases: tuple[str, ...]) -> str | None:
    for key in aliases:
        if key in row and row[key] is not None:
            return row[key]
    return None


def valid_bar(bar: dict[str, Any]) -> bool:
    o, h, l, c, v = (bar.get(k) for k in ("open", "high", "low", "close", "volume"))
    return (bool(bar.get("date")) and bool(bar.get("symbol")) and
            all(isinstance(x, (int, float)) and math.isfinite(x) for x in (o, h, l, c, v)) and
            min(o, h, l, c) > 0 and v >= 0 and h >= max(o, c, l) and l <= min(o, c, h))


def read_bhavcopy_zip(content: bytes, source_date: dt.date) -> tuple[list[dict[str, Any]], list[str]]:
    warnings: list[str] = []
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as exc:
        raise ValueError("NSE response was not a ZIP archive") from exc
    csv_names = [n for n in archive.namelist() if n.lower().endswith(".csv")]
    if not csv_names:
        raise ValueError("ZIP archive contained no CSV")
    raw = archive.read(csv_names[0])
    text = raw.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ValueError("Bhavcopy CSV has no header")
    bars: list[dict[str, Any]] = []
    duplicates = 0
    required_aliases = {
        "date": ("traddt", "timestamp", "date", "traddate", "bizdt"),
        "symbol": ("tckrsymb", "symbol", "tradingsymbol", "scname"),
        "series": ("sctysrs", "series", "securityseries"),
        "open": ("opnpric", "open", "openprice"),
        "high": ("hghpric", "high", "highprice"),
        "low": ("lwpric", "low", "lowprice"),
        "close": ("clspric", "close", "closeprice"),
        "volume": ("ttltradgvol", "tottrdqty", "volume", "totaltradedquantity", "tradedqty"),
    }
    # Normalise keys for stable alias matching.
    normalized_rows = []
    for original in reader:
        normalized_rows.append({clean_header(k): (v or "").strip() for k, v in original.items() if k is not None})
    sample = normalized_rows[0] if normalized_rows else {}
    found = {key: any(alias in sample for alias in aliases) for key, aliases in required_aliases.items()}
    missing = [key for key in ("date", "symbol", "open", "high", "low", "close", "volume") if not found[key]]
    if missing:
        raise ValueError("Unrecognized NSE CM-UDiFF schema; missing: " + ", ".join(missing))
    if not found["series"]:
        raise ValueError("Series column missing; refusing to import without an EQ-series filter")
    seen: set[tuple[str, str]] = set()
    for row in normalized_rows:
        # Cash equity only: avoid duplicate symbol/date records from BE/SM/other series.
        series = row_value(row, required_aliases["series"])
        if str(series or "").strip().upper() != "EQ":
            continue
        date = iso_date(row_value(row, required_aliases["date"]))
        symbol = str(row_value(row, required_aliases["symbol"]) or "").strip().upper()
        bar = {
            "date": date,
            "symbol": symbol,
            "open": num(row_value(row, required_aliases["open"])),
            "high": num(row_value(row, required_aliases["high"])),
            "low": num(row_value(row, required_aliases["low"])),
            "close": num(row_value(row, required_aliases["close"])),
            "volume": num(row_value(row, required_aliases["volume"])),
        }
        if not valid_bar(bar):
            continue
        key = (date, symbol)
        if key in seen:
            duplicates += 1
            continue
        seen.add(key)
        bars.append(bar)
    if duplicates:
        warnings.append(f"{duplicates} duplicate EQ rows skipped in the {source_date.isoformat()} bhavcopy.")
    if not bars:
        raise ValueError("No valid EQ-series OHLCV rows found")
    # The filename date can be the intended report date; record actual bar dates from source.
    return bars, warnings


def read_nifty_daily_csv(content: bytes, source_date: dt.date) -> list[dict[str, Any]]:
    text = content.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ValueError("NSE index daily report has no header")
    rows: list[dict[str, Any]] = []
    for original in reader:
        row = {clean_header(k): (v or "").strip() for k, v in original.items() if k is not None}
        name = str(row.get("indexname") or row.get("index") or row.get("indexname1") or "").strip().upper()
        if name.replace(" ", "") not in {"NIFTY50", "NIFTY50PR"}:
            continue
        date = iso_date(row.get("indexdate") or row.get("date") or row.get("timestamp")) or source_date.isoformat()
        bar = {
            "date": date, "symbol": "NIFTY50",
            "open": num(row.get("openindexvalue") or row.get("openindexval") or row.get("open")),
            "high": num(row.get("highindexvalue") or row.get("highindexval") or row.get("high")),
            "low": num(row.get("lowindexvalue") or row.get("lowindexval") or row.get("low")),
            "close": num(row.get("closingindexvalue") or row.get("closeindexvalue") or row.get("closeindexval") or row.get("close")),
            "volume": 0.0,
        }
        if valid_bar(bar):
            rows.append(bar)
    if not rows:
        raise ValueError("Daily index report contained no valid NIFTY 50 OHLC values")
    return rows


def fetch_nifty_daily(day: dt.date) -> list[dict[str, Any]]:
    """Fetch NSE's dated daily index snapshot; a 404 is normal on holidays."""
    url = f"https://nsearchives.nseindia.com/content/indices/ind_close_all_{day.strftime('%d%m%Y')}.csv"
    for attempt in range(2):
        try:
            content = request_bytes(url, referer="https://www.nseindia.com/all-reports")
            return read_nifty_daily_csv(content, day)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return []
            if exc.code in (403, 429, 500, 502, 503, 504) and attempt == 0:
                time.sleep(0.7)
                continue
            raise RuntimeError(f"NSE NIFTY daily report returned HTTP {exc.code} for {day}") from exc
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            if attempt == 0:
                time.sleep(0.3)
                continue
            raise RuntimeError(f"Unable to fetch/parse NIFTY daily report for {day}: {exc}") from exc
    return []


def build_opener(with_cookies: bool = False):
    handlers: list[Any] = []
    if with_cookies:
        handlers.append(urllib.request.HTTPCookieProcessor(CookieJar()))
    return urllib.request.build_opener(*handlers)


def request_bytes(url: str, *, opener=None, referer: str = NSE_REPORTS) -> bytes:
    opener = opener or urllib.request.build_opener()
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/json,text/csv,application/zip,*/*",
        "Accept-Language": "en-IN,en;q=0.9",
        "Referer": referer,
        "Connection": "keep-alive",
    }
    request = urllib.request.Request(url, headers=headers)
    with opener.open(request, timeout=TIMEOUT) as response:
        return response.read()


def fetch_daily_bhavcopy(day: dt.date) -> tuple[list[dict[str, Any]], list[str]] | None:
    url = NSE_ARCHIVE_TEMPLATE.format(date=day.strftime("%Y%m%d"))
    for attempt in range(3):
        try:
            payload = request_bytes(url)
            bars, warnings = read_bhavcopy_zip(payload, day)
            return bars, warnings
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None  # weekend, holiday, or unpublished archive
            if exc.code in (403, 429, 500, 502, 503, 504) and attempt < 2:
                time.sleep(1.0 * (attempt + 1))
                continue
            raise RuntimeError(f"NSE archive returned HTTP {exc.code} for {day}") from exc
        except (urllib.error.URLError, TimeoutError, ValueError, zipfile.BadZipFile) as exc:
            if attempt < 2:
                time.sleep(0.5 * (attempt + 1))
                continue
            raise RuntimeError(f"Unable to fetch/parse NSE archive for {day}: {exc}") from exc
    return None


def load_history() -> list[dict[str, Any]]:
    if not HISTORY_PATH.exists():
        return []
    rows: list[dict[str, Any]] = []
    with HISTORY_PATH.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            date = iso_date(row.get("date"))
            symbol = str(row.get("symbol") or "").strip().upper()
            bar = {"date": date, "symbol": symbol}
            for key in ("open", "high", "low", "close", "volume"):
                bar[key] = num(row.get(key))
            if valid_bar(bar):
                rows.append(bar)
    return rows


def write_history(rows: list[dict[str, Any]]) -> None:
    # Keep a bounded, rolling window by exchange sessions to retain SMA200 plus buffer.
    equity_dates = sorted({r["date"] for r in rows if not r["symbol"].startswith("NIFTY")})
    if len(equity_dates) > KEEP_SESSIONS:
        cutoff = equity_dates[-KEEP_SESSIONS]
        rows = [r for r in rows if r["date"] >= cutoff]
    rows.sort(key=lambda r: (r["date"], r["symbol"]))
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with HISTORY_PATH.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=HISTORY_COLUMNS, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({
                "date": row["date"], "symbol": row["symbol"],
                "open": f"{row['open']:.2f}", "high": f"{row['high']:.2f}",
                "low": f"{row['low']:.2f}", "close": f"{row['close']:.2f}",
                "volume": f"{row['volume']:.0f}",
            })


def merge_rows(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    dedup: dict[tuple[str, str], dict[str, Any]] = {}
    for group in groups:
        for row in group:
            if valid_bar(row):
                dedup[(row["date"], row["symbol"])] = row
    return sorted(dedup.values(), key=lambda r: (r["date"], r["symbol"]))


def daterange(start: dt.date, end: dt.date):
    day = start
    while day <= end:
        if day.weekday() < 5:
            yield day
        day += dt.timedelta(days=1)


def nse_today() -> dt.date:
    # GitHub Actions is configured to use Asia/Kolkata, but keep date interpretation explicit.
    return dt.datetime.now(dt.timezone(dt.timedelta(hours=5, minutes=30))).date()


def classify_status(trading_sessions: int, latest_market_date: str | None, today: dt.date) -> str:
    """Fail closed until there are 200 sessions and recent, dated equity data."""
    if not latest_market_date or trading_sessions <= 0:
        return "source_unavailable"
    if trading_sessions < 200:
        return "warming_up"
    age_days = (today - dt.date.fromisoformat(latest_market_date)).days
    return "ready" if 0 <= age_days <= 3 else "stale"


def acquire_equities(history: list[dict[str, Any]], force_backfill: bool) -> tuple[list[dict[str, Any]], dict[str, Any], list[str]]:
    warnings: list[str] = []
    today = nse_today()
    equity_dates = sorted({r["date"] for r in history if not r["symbol"].startswith("NIFTY")})
    should_backfill = force_backfill or len(equity_dates) < 205
    downloaded: list[dict[str, Any]] = []
    succeeded_sessions: list[str] = []
    failed: list[str] = []
    consecutive_failures = 0
    if should_backfill:
        # Backfill most recent completed sessions first; stop after the target number of archive days.
        start = today - dt.timedelta(days=510)
        candidate_days = list(daterange(start, today))
        for day in reversed(candidate_days):
            had_error = False
            try:
                result = fetch_daily_bhavcopy(day)
            except Exception as exc:
                had_error = True
                failed.append(f"{day.isoformat()}: {exc}")
                consecutive_failures += 1
                result = None
                if consecutive_failures >= 8:
                    warnings.append("Backfill stopped after 8 consecutive archive access/parse failures; check NSE availability and retry later.")
                    break
            if not had_error:
                consecutive_failures = 0  # 404s are normal for weekends/holidays/unpublished reports.
            if result:
                consecutive_failures = 0
                bars, parse_warnings = result
                downloaded.extend(bars)
                succeeded_sessions.append(day.isoformat())
                warnings.extend(parse_warnings)
                try:
                    downloaded.extend(fetch_nifty_daily(day))
                except Exception as idx_exc:
                    warnings.append(str(idx_exc))
                if len(set(succeeded_sessions)) >= BACKFILL_SESSIONS:
                    break
            time.sleep(0.12)
        if len(set(succeeded_sessions)) < 200:
            warnings.append(f"Initial backfill found only {len(set(succeeded_sessions))} NSE session files; at least 200 sessions are needed for SMA200 signals. Re-run the workflow after checking source access.")
        elif len(set(succeeded_sessions)) < BACKFILL_SESSIONS:
            warnings.append(f"Backfill reached {len(set(succeeded_sessions))} valid sessions, below the {BACKFILL_SESSIONS}-session target.")
    else:
        latest = dt.date.fromisoformat(equity_dates[-1]) if equity_dates else today - dt.timedelta(days=8)
        # Fill small gaps from immediately after last stored session. If history has been stale >14 days,
        # backfill a recent window, rather than trying every weekday indefinitely.
        start = latest + dt.timedelta(days=1) if (today - latest).days <= 14 else today - dt.timedelta(days=14)
        for day in daterange(start, today):
            try:
                result = fetch_daily_bhavcopy(day)
            except Exception as exc:
                failed.append(f"{day.isoformat()}: {exc}")
                result = None
            if result:
                bars, parse_warnings = result
                downloaded.extend(bars)
                succeeded_sessions.append(day.isoformat())
                warnings.extend(parse_warnings)
                try:
                    downloaded.extend(fetch_nifty_daily(day))
                except Exception as idx_exc:
                    warnings.append(str(idx_exc))
            time.sleep(0.1)
    if failed:
        warnings.append(f"{len(failed)} NSE archive request(s) failed. Last failure: {failed[-1]}")
    if not succeeded_sessions:
        warnings.append("No new NSE CM-UDiFF archive was fetched in this run; existing stored history was retained.")
    merged = merge_rows(history, downloaded)
    run = {"attempted_backfill": should_backfill, "downloaded_files": len(set(succeeded_sessions)),
           "downloaded_rows": len(downloaded), "failed_requests": len(failed),
           "downloaded_dates": sorted(set(succeeded_sessions))}
    return merged, run, warnings


def flatten_records(value: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    def walk(node: Any):
        if isinstance(node, list):
            for item in node:
                walk(item)
        elif isinstance(node, dict):
            normal = {clean_header(str(k)): v for k, v in node.items()}
            date_keys = ("timestamp", "chtimestamp", "historicaldate", "date", "eodtimestamp", "indexdate", "traddt")
            close_keys = ("eodcloseindexval", "chclosingprice", "chclosingindexval", "close", "indexclose", "closeindexvalue")
            if any(k in normal for k in date_keys) and any(k in normal for k in close_keys):
                found.append(node)
            for v in node.values():
                if isinstance(v, (list, dict)):
                    walk(v)
    walk(value)
    # Avoid duplicates caused by walking nested objects more than once.
    unique: dict[tuple[str, str], dict[str, Any]] = {}
    for row in found:
        normal = {clean_header(str(k)): v for k, v in row.items()}
        date = None
        close = None
        for k in ("timestamp", "chtimestamp", "historicaldate", "date", "eodtimestamp", "indexdate", "traddt"):
            if k in normal and (date := iso_date(normal[k])):
                break
        for k in ("eodcloseindexval", "chclosingprice", "chclosingindexval", "close", "indexclose", "closeindexvalue"):
            if k in normal and (close := num(normal[k])) is not None:
                break
        if date and close and close > 0:
            unique[(date, str(close))] = row
    return list(unique.values())


def fetch_nifty_history(start: dt.date, end: dt.date) -> tuple[list[dict[str, Any]], str | None]:
    opener = build_opener(with_cookies=True)
    try:
        # Establish NSE cookies before calling the historical API.
        request_bytes("https://www.nseindia.com/", opener=opener, referer="https://www.nseindia.com/")
        request_bytes("https://www.nseindia.com/reports-indices-historical-index-data", opener=opener,
                      referer="https://www.nseindia.com/")
    except Exception:
        # Some edge nodes do not require cookies; still attempt the API below.
        pass
    all_rows: list[dict[str, Any]] = []
    chunk_start = start
    while chunk_start <= end:
        chunk_end = min(end, chunk_start + dt.timedelta(days=150))
        query = urllib.parse.urlencode({"indexType": "NIFTY 50", "from": chunk_start.strftime("%d-%m-%Y"),
                                        "to": chunk_end.strftime("%d-%m-%Y")})
        url = f"{NSE_INDEX_API}?{query}"
        try:
            payload = request_bytes(url, opener=opener, referer="https://www.nseindia.com/reports-indices-historical-index-data")
            decoded = json.loads(payload.decode("utf-8-sig"))
            for row in flatten_records(decoded):
                normal = {clean_header(str(k)): v for k, v in row.items()}
                values: dict[str, Any] = {}
                aliases = {
                    "date": ("timestamp", "chtimestamp", "historicaldate", "date", "eodtimestamp", "indexdate", "traddt"),
                    "open": ("eodopenindexval", "chopeningprice", "chopeningindexval", "open", "indexopen", "openindexvalue"),
                    "high": ("eodhighindexval", "chtradehighprice", "chhighindexval", "high", "indexhigh", "highindexvalue"),
                    "low": ("eodlowindexval", "chtradelowprice", "chlowindexval", "low", "indexlow", "lowindexvalue"),
                    "close": ("eodcloseindexval", "chclosingprice", "chclosingindexval", "close", "indexclose", "closeindexvalue"),
                }
                for key, options in aliases.items():
                    raw = next((normal[x] for x in options if x in normal), None)
                    values[key] = iso_date(raw) if key == "date" else num(raw)
                bar = {"date": values["date"], "symbol": "NIFTY50", "open": values["open"],
                       "high": values["high"], "low": values["low"], "close": values["close"], "volume": 0.0}
                if valid_bar(bar):
                    all_rows.append(bar)
        except Exception as exc:
            return [], f"NIFTY 50 history request failed for {chunk_start} to {chunk_end}: {exc}"
        chunk_start = chunk_end + dt.timedelta(days=1)
        time.sleep(0.1)
    result = merge_rows(all_rows)
    if len({r["date"] for r in result}) < 61:
        return [], f"NIFTY 50 history returned fewer than 61 rows; relative-strength screening remains disabled."
    return result, None


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else math.nan


def indicator_series(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = sorted(rows, key=lambda r: r["date"])
    n = len(rows)
    closes = [float(r["close"]) for r in rows]
    highs = [float(r["high"]) for r in rows]
    lows = [float(r["low"]) for r in rows]
    vols = [float(r["volume"]) for r in rows]
    ema20: list[float] = []
    for i, value in enumerate(closes):
        ema20.append(value if i == 0 else (2 / 21) * value + (19 / 21) * ema20[i - 1])
    rsi = [math.nan] * n
    if n > 14:
        gains = [max(0.0, closes[i] - closes[i - 1]) for i in range(1, n)]
        losses = [max(0.0, closes[i - 1] - closes[i]) for i in range(1, n)]
        avg_gain = sum(gains[:14]) / 14
        avg_loss = sum(losses[:14]) / 14
        def rsi_value(g: float, l: float) -> float:
            return 100.0 if l == 0 and g > 0 else (50.0 if l == 0 else 100 - 100 / (1 + g / l))
        rsi[14] = rsi_value(avg_gain, avg_loss)
        for i in range(15, n):
            avg_gain = (avg_gain * 13 + gains[i - 1]) / 14
            avg_loss = (avg_loss * 13 + losses[i - 1]) / 14
            rsi[i] = rsi_value(avg_gain, avg_loss)
    tr: list[float] = []
    for i in range(n):
        prev = closes[i - 1] if i else closes[i]
        tr.append(max(highs[i] - lows[i], abs(highs[i] - prev), abs(lows[i] - prev)))
    out = []
    for i, row in enumerate(rows):
        def sma(a: list[float], length: int) -> float:
            return mean(a[i - length + 1:i + 1]) if i + 1 >= length else math.nan
        avg_vol = sma(vols, 20)
        avg_turn = mean([closes[j] * vols[j] for j in range(i - 19, i + 1)]) if i >= 19 else math.nan
        out.append({**row,
            "sma20": sma(closes, 20), "sma50": sma(closes, 50), "sma200": sma(closes, 200),
            "ema20": ema20[i], "rsi": rsi[i],
            "prior_high20": max(highs[i - 20:i]) if i >= 20 else math.nan,
            "avg_vol": avg_vol, "avg_turn": avg_turn,
            "rel_vol": vols[i] / avg_vol if i >= 19 and avg_vol > 0 else math.nan,
            "momentum60": closes[i] / closes[i - 60] - 1 if i >= 60 and closes[i - 60] > 0 else math.nan,
            "atr": mean(tr[i - 13:i + 1]) if i >= 13 else math.nan,
            "prev_close": closes[i - 1] if i > 0 else math.nan,
            "prev_ema20": ema20[i - 1] if i > 0 else math.nan,
        })
    return out


def corporate_action_symbols(by_symbol: dict[str, list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    flags: dict[str, list[dict[str, Any]]] = {}
    for symbol, rows in by_symbol.items():
        if symbol.startswith("NIFTY"):
            continue
        rows = sorted(rows, key=lambda r: r["date"])
        events = []
        for prev, cur in zip(rows, rows[1:]):
            if prev["close"] > 0:
                pct = (cur["close"] / prev["close"] - 1) * 100
                if abs(pct) >= 40:
                    events.append({"date": cur["date"], "pct": round(pct, 2)})
        if events:
            flags[symbol] = events
    return flags


def screen(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any], list[str]]:
    by_symbol: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_symbol[row["symbol"]].append(row)
    equity_dates = sorted({r["date"] for r in rows if not r["symbol"].startswith("NIFTY")})
    latest_date = equity_dates[-1] if equity_dates else None
    benchmark_rows = by_symbol.get("NIFTY50", []) or by_symbol.get("NIFTY_50", [])
    benchmark_ind = indicator_series(benchmark_rows) if benchmark_rows else []
    bench_momentum = {r["date"]: r["momentum60"] for r in benchmark_ind}
    jump_flags = corporate_action_symbols(by_symbol)
    candidates: list[dict[str, Any]] = []
    eligible = 0
    long_history = 0
    stale_count = 0
    turn_count = 0
    for symbol, raw in by_symbol.items():
        if symbol.startswith("NIFTY") or symbol == "DEMO_INDEX":
            continue
        inds = indicator_series(raw)
        if not inds:
            continue
        current = inds[-1]
        if not current["date"] or not latest_date:
            continue
        # The automated shortlist is for the latest equity session only. Do not
        # surface an otherwise-qualifying setup from an older per-symbol bar.
        if current["date"] != latest_date:
            stale_count += 1
            continue
        eligible += 1
        if len(inds) < 200 or not math.isfinite(current["sma200"]):
            continue
        long_history += 1
        if symbol in jump_flags:
            continue
        if not math.isfinite(current["atr"]) or not math.isfinite(current["sma50"]) or not math.isfinite(current["rsi"]) or not math.isfinite(current["avg_turn"]):
            continue
        if current["avg_turn"] < MIN_AVG_TURNOVER:
            continue
        turn_count += 1
        trend = current["close"] > current["sma50"] > current["sma200"]
        breakout = (current["close"] > current["prior_high20"] and current["rel_vol"] >= 1.5 and
                    52 <= current["rsi"] <= 72 and trend)
        near_ema = abs(current["close"] - current["ema20"]) / current["ema20"] <= .025 if current["ema20"] else False
        pullback = (trend and near_ema and current["close"] > current["ema20"] and
                    current["prev_close"] <= current["prev_ema20"] and 42 <= current["rsi"] <= 62)
        stock_mom = current["momentum60"]
        bm_mom = bench_momentum.get(current["date"], math.nan)
        relative = (math.isfinite(stock_mom) and math.isfinite(bm_mom) and stock_mom > bm_mom and
                    trend and current["close"] > current["prior_high20"])
        strategies = []
        if breakout:
            strategies.append("breakout")
        if pullback:
            strategies.append("pullback")
        if relative:
            strategies.append("relative_strength")
        if not strategies:
            continue
        trend_score = max(0, min(100, (current["close"] / current["sma50"] - 1) * 500))
        volume_score = max(0, min(100, current["rel_vol"] * 30))
        rsi_score = max(0, 100 - abs(current["rsi"] - 60) * 3)
        momentum_score = max(0, min(100, ((stock_mom if math.isfinite(stock_mom) else 0) + .1) * 500))
        score = round(.25 * trend_score + .2 * volume_score + .25 * rsi_score + .3 * momentum_score)
        atr = current["atr"]
        candidates.append({
            "symbol": symbol, "date": current["date"], "strategy": strategies[0], "strategies": strategies,
            "close": round(current["close"], 4), "rsi": round(current["rsi"], 4),
            "rel_vol": round(current["rel_vol"], 4), "score": int(score),
            "stop": round(current["close"] - 2 * atr, 4), "atr14_sma": round(atr, 4),
            "avg_turnover_20d": round(current["avg_turn"], 2),
            "return_60d": round(stock_mom * 100, 2) if math.isfinite(stock_mom) else None,
            "benchmark_return_60d": round(bm_mom * 100, 2) if math.isfinite(bm_mom) else None,
        })
    candidates.sort(key=lambda r: (-r["score"], r["symbol"]))
    warnings: list[str] = []
    if len(equity_dates) < 200:
        warnings.append(f"Only {len(equity_dates)} equity sessions are stored; SMA200 filtering needs at least 200. Screening is not ready.")
    if not benchmark_rows:
        warnings.append("NIFTY 50 benchmark history is missing. Relative-strength setups are disabled; breakout and pullback still screen.")
    elif benchmark_rows and max(r["date"] for r in benchmark_rows) < (latest_date or ""):
        warnings.append(f"NIFTY 50 benchmark is behind equity history (benchmark through {max(r['date'] for r in benchmark_rows)}); relative-strength signals may be absent.")
    if jump_flags:
        preview = ", ".join(f"{s} ({len(events)} move(s))" for s, events in list(sorted(jump_flags.items()))[:12])
        warnings.append(f"{len(jump_flags)} symbol(s) with a close move of at least 40% are excluded until adjusted history is reviewed: {preview}{' …' if len(jump_flags)>12 else ''}.")
    warnings.append("Daily bhavcopy prices may be unadjusted for splits/bonuses. Corporate-action flags are conservative exclusions, not automatic adjustments.")
    warnings.append("Screening is EOD-only and rule-based. Scores are not profit probabilities; no orders are sent.")
    meta = {"latest_market_date": latest_date, "history_start": equity_dates[0] if equity_dates else None,
            "trading_sessions": len(equity_dates), "bars_count": len(rows),
            "symbols_count": len([s for s in by_symbol if not s.startswith("NIFTY")]),
            "eligible_symbols": eligible, "symbols_with_200_sessions": long_history,
            "symbols_over_turnover_floor": turn_count, "stale_symbols_skipped": stale_count,
            "corporate_action_flagged_symbols": len(jump_flags),
            "benchmark_latest_date": max((r["date"] for r in benchmark_rows), default=None),
            "strategy_counts": {k: sum(1 for r in candidates if k in r["strategies"]) for k in ("breakout", "pullback", "relative_strength")}}
    return candidates, meta, warnings


def write_json(path: Path, content: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(content, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")


def main() -> int:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    started = dt.datetime.now(dt.timezone.utc)
    force_backfill = str(os.environ.get("FORCE_BACKFILL", "false")).lower() in {"1", "true", "yes", "on"}
    existing = load_history()
    history, equity_run, warnings = acquire_equities(existing, force_backfill)
    equity_dates = sorted({r["date"] for r in history if not r["symbol"].startswith("NIFTY")})
    today = nse_today()
    # The normal benchmark source is NSE's dated daily index snapshot, fetched alongside bhavcopy.
    # Refresh the history API both when there are fewer than 61 rows and when its last date
    # is behind the latest equity session. Count alone must not leave RS permanently stale.
    benchmark_rows = [r for r in history if r["symbol"] == "NIFTY50"]
    benchmark_dates = {r["date"] for r in benchmark_rows}
    latest_equity_date = max(equity_dates, default=None)
    latest_benchmark_date = max(benchmark_dates, default=None)
    benchmark_error = None
    needs_benchmark_refresh = len(benchmark_dates) < 61 or (latest_equity_date and (not latest_benchmark_date or latest_benchmark_date < latest_equity_date))
    if needs_benchmark_refresh:
        fallback_rows, benchmark_error = fetch_nifty_history(today - dt.timedelta(days=480), today)
        if len({r["date"] for r in fallback_rows}) >= 61:
            history = [r for r in history if r["symbol"] != "NIFTY50"] + fallback_rows
            benchmark_rows = fallback_rows
        elif benchmark_error:
            warnings.append(benchmark_error)
            # Keep any previously stored benchmark rows, even if it is incomplete.
    history = merge_rows(history)
    write_history(history)
    # Re-read normalized persisted history so the screen reflects precisely what was committed.
    history = load_history()
    candidates, meta, screen_warnings = screen(history)
    warnings.extend(screen_warnings)
    warnings = list(dict.fromkeys(warnings))
    equity_date = meta["latest_market_date"]
    status = classify_status(meta["trading_sessions"], equity_date, today)
    stale_days = (today - dt.date.fromisoformat(equity_date)).days if equity_date else 999
    # Fail closed: never publish stock candidates as current when the history gate fails.
    if status != "ready":
        candidates = []
        meta["strategy_counts"] = {"breakout": 0, "pullback": 0, "relative_strength": 0}
    if equity_date and (stale_days < 0 or stale_days > 3):
        warnings.append(f"Latest stored equity session is {stale_days} calendar days from today ({equity_date}); only data 0–3 calendar days old can pass the freshness gate. Check NSE archive availability and rerun the workflow.")
    payload = {
        "schema_version": 1,
        "status": status,
        "source": "NSE CM-UDiFF Common Bhavcopy (EQ series) + NSE daily NIFTY 50 snapshot (historical API fallback)",
        "source_urls": {"equity_bhavcopy": "https://www.nseindia.com/all-reports", "index_daily_snapshots": "https://www.nseindia.com/all-reports", "index_history_fallback": "https://www.nseindia.com/reports-indices-historical-index-data"},
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "as_of_ist": dt.datetime.now(dt.timezone(dt.timedelta(hours=5, minutes=30))).isoformat(timespec="seconds"),
        "latest_market_date": meta["latest_market_date"], "history_start": meta["history_start"],
        "trading_sessions": meta["trading_sessions"], "bars_count": meta["bars_count"],
        "symbols_count": meta["symbols_count"], "eligible_symbols": meta["eligible_symbols"],
        "symbols_with_200_sessions": meta["symbols_with_200_sessions"],
        "symbols_over_turnover_floor": meta["symbols_over_turnover_floor"],
        "stale_symbols_skipped": meta["stale_symbols_skipped"],
        "corporate_action_flagged_symbols": meta["corporate_action_flagged_symbols"],
        "benchmark_latest_date": meta["benchmark_latest_date"],
        "qualified_count": len(candidates), "strategy_counts": meta["strategy_counts"],
        "filters": {"min_avg_turnover_20_sessions_inr": MIN_AVG_TURNOVER, "trend": "close > SMA50 > SMA200",
                    "breakout": "close > prior 20-session high; relative volume >= 1.5; RSI 52-72",
                    "pullback": "uptrend; close above EMA20 within 2.5%; prior close at/below prior EMA20; RSI 42-62",
                    "relative_strength": "60-session return > NIFTY 50; uptrend; new 20-session high",
                    "stop_estimate": "close - 2 × simple-average ATR(14)"},
        "warnings": warnings,
        "candidates": candidates,
    }
    write_json(SCREEN_PATH, payload)
    run = {"started_at_utc": started.isoformat(timespec="seconds"),
           "completed_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
           "status": status, "equity_ingestion": equity_run,
           "benchmark_rows_received": len(benchmark_rows), "benchmark_error": benchmark_error,
           "history_rows_after_dedup": len(history), "screened_candidates": len(candidates),
           "warnings": warnings[:80]}
    write_json(RUN_PATH, run)
    print(json.dumps(run, indent=2))
    if status == "source_unavailable":
        print("::warning::No usable NSE history is available yet. Inspect the source/HTTP warnings in data/last_run.json and retry the workflow.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
