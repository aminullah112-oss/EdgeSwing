#!/usr/bin/env python3
"""Multi-year NSE EOD archive for research backtests.

The live pipeline keeps a rolling ~320-session data/history.csv. Research needs several
market regimes, so older sessions live in write-once monthly files:

    data/archive/YYYY-MM.csv.gz    same columns as history.csv (incl. NIFTY50 rows)

A month file is written only when complete and is never rewritten, so the daily job
adds no git churn. Old files also contain stocks that later delisted, which reduces
survivorship bias versus the current-universe history.

Usage:
    python archive_nse.py backfill 2021-01 2025-09   # download months from NSE (one-off, ~15 min)
    python archive_nse.py sync                        # archive completed months found in history.csv
Sources: CM-UDiFF bhavcopy (2024+) and the older cmDDMONYYYYbhav.csv format (before 2024),
plus NSE's daily ind_close_all index snapshot for NIFTY 50.
"""
from __future__ import annotations

import csv
import datetime as dt
import gzip
import io
import sys
import time
import urllib.error
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import update_nse_data as core  # noqa: E402

ARCHIVE_DIR = core.DATA_DIR / "archive"
OLD_TEMPLATE = ("https://nsearchives.nseindia.com/content/historical/EQUITIES/{yyyy}/{mon}/"
                "cm{dd}{mon}{yyyy}bhav.csv.zip")
UDIFF_START = dt.date(2024, 1, 1)
MIN_SESSIONS_PER_MONTH = 15  # fewer means access failures; leave the month for a retry


def month_path(ym: str) -> Path:
    return ARCHIVE_DIR / f"{ym}.csv.gz"


def fetch_old_format(day: dt.date) -> tuple[list[dict[str, Any]], list[str]] | None:
    mon = day.strftime("%b").upper()
    url = OLD_TEMPLATE.format(yyyy=day.year, mon=mon, dd=day.strftime("%d"))
    for attempt in range(3):
        try:
            return core.read_bhavcopy_zip(core.request_bytes(url), day)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            if attempt == 2:
                raise RuntimeError(f"HTTP {exc.code} for {url}") from exc
        except (urllib.error.URLError, TimeoutError, ValueError, zipfile.BadZipFile) as exc:
            if attempt == 2:
                raise RuntimeError(f"Unable to fetch/parse {url}: {exc}") from exc
        time.sleep(1.0 * (attempt + 1))
    return None


def fetch_day(day: dt.date) -> list[dict[str, Any]] | None:
    """Equity bars for one session in either archive format, or None for a non-trading day."""
    result = core.fetch_daily_bhavcopy(day) if day >= UDIFF_START else None
    if result is None:
        result = fetch_old_format(day)
    return result[0] if result else None


def write_month(ym: str, rows: list[dict[str, Any]]) -> Path:
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    path = month_path(ym)
    tmp = path.with_suffix(".tmp")
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=core.HISTORY_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for r in sorted(rows, key=lambda x: (x["date"], x["symbol"])):
        writer.writerow({"date": r["date"], "symbol": r["symbol"],
                         "open": f"{r['open']:.2f}", "high": f"{r['high']:.2f}", "low": f"{r['low']:.2f}",
                         "close": f"{r['close']:.2f}", "volume": f"{r['volume']:.0f}",
                         "value": f"{r['value']:.0f}" if r.get("value") else ""})
    # mtime=0 keeps the gzip bytes deterministic, so an unchanged month never shows a git diff.
    with open(tmp, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0, compresslevel=9) as gz:
        gz.write(buf.getvalue().encode("utf-8"))
    tmp.replace(path)
    return path


def load_archive(since: str | None = None) -> list[dict[str, Any]]:
    """All archived bars (optionally from YYYY-MM onwards), parsed like history.csv."""
    rows: list[dict[str, Any]] = []
    if not ARCHIVE_DIR.exists():
        return rows
    for path in sorted(ARCHIVE_DIR.glob("*.csv.gz")):
        if since and path.name[:7] < since:
            continue
        with gzip.open(path, "rt", encoding="utf-8", newline="") as fh:
            for r in csv.DictReader(fh):
                bar = {"date": r["date"], "symbol": r["symbol"]}
                for k in ("open", "high", "low", "close", "volume"):
                    bar[k] = core.num(r[k])
                if core.valid_bar(bar):
                    bar["value"] = core.checked_value(core.num(r.get("value")), bar)
                    rows.append(bar)
    return rows


def load_by_symbol(overrides: list[dict[str, Any]] | None = None) -> dict[str, dict[str, tuple]]:
    """symbol -> {date: (open, high, low, close, volume, value)} from the archive, with rows in
    `overrides` (normally history.csv) replacing archived rows for the same date. Compact tuples
    keep five years of every listed stock within a few hundred MB."""
    out: dict[str, dict[str, tuple]] = defaultdict(dict)
    intern: dict[str, str] = {}
    if ARCHIVE_DIR.exists():
        for path in sorted(ARCHIVE_DIR.glob("*.csv.gz")):
            with gzip.open(path, "rt", encoding="utf-8", newline="") as fh:
                for r in csv.DictReader(fh):
                    o, h, l, c, v = (core.num(r[k]) for k in ("open", "high", "low", "close", "volume"))
                    bar = {"open": o, "high": h, "low": l, "close": c, "volume": v}
                    if not core.valid_bar({**bar, "date": r["date"], "symbol": r["symbol"]}):
                        continue
                    d = intern.setdefault(r["date"], r["date"])
                    out[r["symbol"]][d] = (o, h, l, c, v, core.checked_value(core.num(r.get("value")), bar))
    for r in overrides or []:
        d = intern.setdefault(r["date"], r["date"])
        out[r["symbol"]][d] = (r["open"], r["high"], r["low"], r["close"], r["volume"], r.get("value"))
    return out


def months_between(start: str, end: str) -> list[str]:
    y, m = map(int, start.split("-"))
    ey, em = map(int, end.split("-"))
    out = []
    while (y, m) <= (ey, em):
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def backfill(start: str, end: str, pause: float = 0.12) -> dict[str, Any]:
    etfs = core.load_cached_etf_symbols()
    done, skipped, failed = [], [], []
    for ym in months_between(start, end):
        if month_path(ym).exists():
            skipped.append(ym)
            continue
        y, m = map(int, ym.split("-"))
        first = dt.date(y, m, 1)
        last = (dt.date(y + (m == 12), m % 12 + 1, 1) - dt.timedelta(days=1))
        rows: list[dict[str, Any]] = []
        sessions = 0
        errors = 0
        for day in core.daterange(first, last):
            try:
                bars = fetch_day(day)
            except Exception as exc:  # keep going; the month is retried if it ends up short
                errors += 1
                print(f"  {day}: {exc}", file=sys.stderr)
                bars = None
            if bars:
                sessions += 1
                rows.extend(b for b in bars if b["symbol"] not in etfs)
                try:
                    rows.extend(core.fetch_nifty_daily(day))
                except Exception as exc:
                    print(f"  {day} NIFTY: {exc}", file=sys.stderr)
            time.sleep(pause)
        if sessions >= MIN_SESSIONS_PER_MONTH:
            write_month(ym, core.merge_rows(rows))
            done.append(ym)
            print(f"{ym}: {sessions} sessions, {len(rows)} rows" + (f", {errors} errors" if errors else ""), flush=True)
        else:
            failed.append(ym)
            print(f"{ym}: only {sessions} sessions ({errors} errors); not written, retry later", flush=True)
    return {"written": done, "existing": skipped, "failed": failed}


def sync_from_history(history: list[dict[str, Any]], today: dt.date | None = None) -> list[str]:
    """Archive months that are complete and fully covered by history.csv (no downloads)."""
    today = today or core.nse_today()
    current = today.strftime("%Y-%m")
    by_month: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in history:
        by_month[r["date"][:7]].append(r)
    equity_dates = sorted({r["date"] for r in history if not r["symbol"].startswith("NIFTY")})
    if not equity_dates:
        return []
    start = equity_dates[0]
    written = []
    for ym, rows in sorted(by_month.items()):
        # Complete (before the current month) and fully inside history (history began in an earlier month).
        if ym >= current or start[:7] >= ym or month_path(ym).exists():
            continue
        write_month(ym, rows)
        written.append(ym)
    return written


def main(argv: list[str]) -> int:
    if len(argv) >= 2 and argv[1] == "backfill":
        start, end = (argv[2], argv[3]) if len(argv) >= 4 else ("2021-01", (core.nse_today().replace(day=1) - dt.timedelta(days=1)).strftime("%Y-%m"))
        result = backfill(start, end)
        print(result)
        return 0 if not result["failed"] else 2
    if len(argv) >= 2 and argv[1] == "sync":
        print({"archived_from_history": sync_from_history(core.load_history())})
        return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
