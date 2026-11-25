#!/usr/bin/env python3
"""Build the static market-explorer dataset from the stored NSE EOD history.

Reads ``data/history.csv`` (written by update_nse_data.py) and publishes, under
``data/market/`` by default:

* ``snapshot.json``   one row per stock: latest price, returns, 52-week range,
                      moving averages, RSI, MACD, ATR, liquidity and event flags.
* ``breadth.json``    per-session advances/declines, % of stocks above their
                      50/200-session averages, new 52-week highs/lows and NIFTY 50.
* ``stocks/<key>.json`` full stored OHLCV history for one symbol, for charts.

Company names, industries and size buckets come from NSE's published index
constituent lists. A committed cache (``data/index_constituents.csv``) is used
when NSE is unreachable, so a transient download failure never empties the
sector views.

The output is derived data and is not committed; the workflow builds it into the
GitHub Pages artifact. Run locally with ``python .github/scripts/build_market_data.py``.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import json
import math
import re
import shutil
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import update_nse_data as core  # noqa: E402  (shared loaders, HTTP helper, CA rule)
import fundamentals as fund  # noqa: E402
import conviction as conv  # noqa: E402
import se_scans as se  # noqa: E402

ROOT = core.ROOT
DEFAULT_OUT = ROOT / "data" / "market"
CONSTITUENTS_CACHE = ROOT / "data" / "index_constituents.csv"
FUNDAMENTALS_PATH = ROOT / "data" / "fundamentals.csv"
EQUITY_LIST_URL = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"
EQUITY_LIST_CACHE = ROOT / "data" / "nse_equity_list.csv"
MIN_EQUITY_LIST = 1500
FUND_COLUMNS = ["pe", "eps", "mcap", "pb", "dy", "roe", "roce", "de", "fa"]
CONSTITUENT_COLUMNS = ("symbol", "name", "industry", "cap", "nifty50")
INDEX_BASE = "https://nsearchives.nseindia.com/content/indices/"
# Ordered: a symbol takes the bucket of the first list it appears in.
INDEX_LISTS = (
    ("large", "ind_nifty100list.csv"),
    ("mid", "ind_niftymidcap150list.csv"),
    ("small", "ind_niftysmallcap250list.csv"),
    ("micro", "ind_niftymicrocap250_list.csv"),
)
NIFTY50_LIST = "ind_nifty50list.csv"
MIN_CONSTITUENTS = 650  # sanity floor before overwriting the cache (expected ~750)
BENCHMARK = "NIFTY50"
WEEK52 = 250            # sessions in a 52-week window
RETURN_WINDOWS = {"r1w": 5, "r1m": 21, "r3m": 63, "r6m": 126, "r1y": 250}
CROSS_LOOKBACK = 5      # sessions for "recent" golden/death cross events
SAFE_KEY = re.compile(r"[^A-Z0-9-]")

SNAPSHOT_COLUMNS = [
    "s", "n", "ind", "cap", "n50", "d", "o", "h", "l", "c", "ch", "v", "t", "at", "rv",
    "r1w", "r1m", "r3m", "r6m", "r1y", "h52", "l52", "dh", "dl",
    "s20", "s50", "s200", "e20", "rsi", "atrp", "macd", "macds", "macdh", "gap",
    "ev", "ca", "stale", "key", "len", "vw", "vw20", "cv", "cvf", "hc", "adx", "rs55", "se", "seb", "ser",
] + FUND_COLUMNS


# --------------------------------------------------------------------------- constituents

def parse_constituents(content: bytes) -> list[dict[str, str]]:
    """Parse an NSE index constituent CSV (Company Name, Industry, Symbol, Series, ISIN)."""
    text = content.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ValueError("constituent CSV has no header")
    header = {core.clean_header(h): h for h in reader.fieldnames}
    need = {"companyname", "industry", "symbol"}
    if not need <= set(header):
        raise ValueError(f"unexpected constituent CSV header: {reader.fieldnames}")
    rows = []
    for raw in reader:
        symbol = (raw.get(header["symbol"]) or "").strip().upper()
        if not symbol:
            continue
        rows.append({"symbol": symbol,
                     "name": (raw.get(header["companyname"]) or "").strip(),
                     "industry": (raw.get(header["industry"]) or "").strip() or "Unclassified"})
    return rows


def load_constituent_cache() -> dict[str, dict[str, Any]]:
    if not CONSTITUENTS_CACHE.exists():
        return {}
    with CONSTITUENTS_CACHE.open(newline="", encoding="utf-8") as fh:
        return {r["symbol"]: {**r, "nifty50": r.get("nifty50") == "1"} for r in csv.DictReader(fh)}


def write_constituent_cache(mapping: dict[str, dict[str, Any]]) -> None:
    with CONSTITUENTS_CACHE.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CONSTITUENT_COLUMNS)
        writer.writeheader()
        for symbol in sorted(mapping):
            row = mapping[symbol]
            writer.writerow({**{k: row[k] for k in CONSTITUENT_COLUMNS[:-1]},
                             "nifty50": "1" if row["nifty50"] else "0"})


def fetch_constituents(fetch=core.request_bytes) -> tuple[dict[str, dict[str, Any]], str, str | None]:
    """Return (symbol -> info, source label, warning). Falls back to the committed cache."""
    try:
        mapping: dict[str, dict[str, Any]] = {}
        for cap, filename in INDEX_LISTS:
            for row in parse_constituents(fetch(INDEX_BASE + filename)):
                mapping.setdefault(row["symbol"], {**row, "cap": cap, "nifty50": False})
        for row in parse_constituents(fetch(INDEX_BASE + NIFTY50_LIST)):
            if row["symbol"] in mapping:
                mapping[row["symbol"]]["nifty50"] = True
        if len(mapping) < MIN_CONSTITUENTS:
            raise ValueError(f"only {len(mapping)} constituents parsed")
        write_constituent_cache(mapping)
        return mapping, "NSE index constituent lists", None
    except Exception as exc:  # network, HTTP or format change
        cached = load_constituent_cache()
        note = (f"NSE index constituent lists unavailable ({type(exc).__name__}: {exc}); "
                + (f"using cached classification for {len(cached)} symbols." if cached
                   else "no cache exists, so names, industries and size buckets are blank."))
        return cached, "cached index constituent lists" if cached else "none", note


def fetch_equity_list(fetch=core.request_bytes) -> tuple[list[dict[str, str]], str | None]:
    """NSE's full EQ list (symbol, company name, ISIN), cached like the constituents."""
    try:
        rows = fund.parse_equity_list(fetch(EQUITY_LIST_URL))
        if len(rows) < MIN_EQUITY_LIST:
            raise ValueError(f"only {len(rows)} securities parsed")
        with EQUITY_LIST_CACHE.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=("symbol", "name", "isin"))
            writer.writeheader()
            writer.writerows(sorted(rows, key=lambda r: r["symbol"]))
        return rows, None
    except Exception as exc:
        if EQUITY_LIST_CACHE.exists():
            with EQUITY_LIST_CACHE.open(newline="", encoding="utf-8") as fh:
                cached = list(csv.DictReader(fh))
            return cached, f"NSE equity list unavailable ({type(exc).__name__}); using cached list of {len(cached)}."
        return [], f"NSE equity list unavailable ({type(exc).__name__}: {exc}); company names limited to index members."


def file_commit_date(path: Path) -> str | None:
    """Last git commit date of a file (YYYY-MM-DD), used when a CSV has no as_of column."""
    import subprocess
    try:
        out = subprocess.run(["git", "log", "-1", "--format=%cs", "--", str(path)], cwd=ROOT,
                             capture_output=True, text=True, timeout=10).stdout.strip()
        return out or None
    except Exception:
        return None


def load_fundamentals(known: set[str], equity_list: list[dict[str, str]],
                      constituents: dict[str, dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], list[str], str | None]:
    """Return (symbol -> export row, warnings, default as_of)."""
    if not FUNDAMENTALS_PATH.exists():
        return {}, [], None
    try:
        rows, warnings = fund.parse_fundamentals_csv(FUNDAMENTALS_PATH.read_bytes(), core.iso_date)
    except ValueError as exc:
        return {}, [f"data/fundamentals.csv ignored: {exc}"], None
    isin_map = {r["isin"]: r["symbol"] for r in equity_list if r.get("isin")}
    name_map: dict[str, str] = {}
    for r in equity_list:
        name_map.setdefault(fund.norm_name(r["name"]), r["symbol"])
    for sym, info in constituents.items():
        name_map.setdefault(fund.norm_name(info.get("name", "")), sym)
    resolved, more = fund.resolve_symbols(rows, known, isin_map, name_map)
    return resolved, warnings + more, file_commit_date(FUNDAMENTALS_PATH)


def close_on_or_before(s: dict[str, list[float]], day: str | None) -> float | None:
    if not day:
        return s["c"][-1] if s["c"] else None
    for d, c in zip(reversed(s["date"]), reversed(s["c"])):
        if d <= day:
            return c
    return None


def fundamentals_for(row: dict[str, Any] | None, s: dict[str, list[float]], jumps: list[dict[str, Any]],
                     default_as_of: str | None) -> tuple[dict[str, Any], str | None]:
    """Daily ratios for one symbol, or blanks with a reason when the export is unusable."""
    blank = {k: None for k in FUND_COLUMNS}
    if not row:
        return blank, None
    as_of = row["as_of"] or default_as_of
    later_jump = next((j["date"] for j in jumps if as_of and j["date"] > as_of), None)
    if later_jump:
        return {**blank, "fa": as_of}, later_jump
    base = fund.base_values(row, close_on_or_before(s, as_of))
    out = {k: rnd(v, 2) for k, v in fund.daily_ratios(base, s["c"][-1]).items()}
    out["mcap"] = rnd(out["mcap"], 0) if out["mcap"] is not None else None
    out["fa"] = as_of
    return out, None


# --------------------------------------------------------------------------- indicators

def finite(x: Any) -> bool:
    return isinstance(x, (int, float)) and math.isfinite(x)


def rnd(x: Any, digits: int = 2) -> float | None:
    return round(float(x), digits) if finite(x) else None


def rolling_mean(values: list[float], length: int) -> list[float]:
    out = [math.nan] * len(values)
    total = 0.0
    for i, v in enumerate(values):
        total += v
        if i >= length:
            total -= values[i - length]
        if i >= length - 1:
            out[i] = total / length
    return out


def ema(values: list[float], length: int) -> list[float]:
    k = 2 / (length + 1)
    out: list[float] = []
    for i, v in enumerate(values):
        out.append(v if i == 0 else k * v + (1 - k) * out[-1])
    return out


def wilder_rsi(closes: list[float], length: int = 14) -> list[float]:
    """Same definition as update_nse_data.indicator_series (Wilder smoothing, seed = SMA)."""
    n = len(closes)
    out = [math.nan] * n
    if n <= length:
        return out
    gains = [max(0.0, closes[i] - closes[i - 1]) for i in range(1, n)]
    losses = [max(0.0, closes[i - 1] - closes[i]) for i in range(1, n)]
    g = sum(gains[:length]) / length
    l = sum(losses[:length]) / length
    def value(g: float, l: float) -> float:
        return 100.0 if l == 0 and g > 0 else (50.0 if l == 0 else 100 - 100 / (1 + g / l))
    out[length] = value(g, l)
    for i in range(length + 1, n):
        g = (g * (length - 1) + gains[i - 1]) / length
        l = (l * (length - 1) + losses[i - 1]) / length
        out[i] = value(g, l)
    return out


def rolling_extreme(values: list[float], length: int, fn) -> list[float]:
    """fn (max or min) over the trailing `length` values including i (shorter at the start).
    Monotonic deque: O(n), needed for multi-year research series."""
    from collections import deque
    better = (lambda a, b: a >= b) if fn is max else (lambda a, b: a <= b)
    dq: deque[int] = deque()
    out = []
    for i, v in enumerate(values):
        while dq and better(v, values[dq[-1]]):
            dq.pop()
        dq.append(i)
        if dq[0] <= i - length:
            dq.popleft()
        out.append(values[dq[0]])
    return out


def series(rows: list[dict[str, Any]]) -> dict[str, list[float]]:
    rows = sorted(rows, key=lambda r: r["date"])
    o = [float(r["open"]) for r in rows]
    h = [float(r["high"]) for r in rows]
    l = [float(r["low"]) for r in rows]
    c = [float(r["close"]) for r in rows]
    v = [float(r["volume"]) for r in rows]
    tr = [max(h[i] - l[i], abs(h[i] - (c[i - 1] if i else c[i])), abs(l[i] - (c[i - 1] if i else c[i])))
          for i in range(len(c))]
    macd = [a - b for a, b in zip(ema(c, 12), ema(c, 26))]
    signal = ema(macd, 9)
    val = [r.get("value") or None for r in rows]
    vw20 = [math.nan] * len(c)
    for i in range(core.VWAP_SESSIONS - 1, len(c)):
        window = range(i - core.VWAP_SESSIONS + 1, i + 1)
        if all(val[j] for j in window):
            vol = sum(v[j] for j in window)
            vw20[i] = sum(val[j] for j in window) / vol if vol > 0 else math.nan
    return {
        "date": [r["date"] for r in rows], "o": o, "h": h, "l": l, "c": c, "v": v, "val": val,
        "vw": [x / y if x and y > 0 else math.nan for x, y in zip(val, v)], "vw20": vw20,
        "s20": rolling_mean(c, 20), "s50": rolling_mean(c, 50), "s200": rolling_mean(c, 200),
        "e20": ema(c, 20), "rsi": wilder_rsi(c), "atr": rolling_mean(tr, 14),
        "avgv": rolling_mean(v, 20), "avgt": rolling_mean([a * b for a, b in zip(c, v)], 20),
        "macd": macd, "macds": signal,
        "h52": rolling_extreme(h, WEEK52, max), "l52": rolling_extreme(l, WEEK52, min),
    }


def window_has_jump(dates: list[str], jumps: set[str], start_index: int) -> bool:
    """True when a flagged corporate-action/data jump falls inside (start_index, end]."""
    return any(d in jumps for d in dates[start_index + 1:])


def crossed(a: list[float], b: list[float], i: int, up: bool) -> bool:
    if i < 1 or not all(finite(x) for x in (a[i], b[i], a[i - 1], b[i - 1])):
        return False
    return (a[i - 1] <= b[i - 1] and a[i] > b[i]) if up else (a[i - 1] >= b[i - 1] and a[i] < b[i])


def events_for(s: dict[str, list[float]]) -> list[str]:
    """Event codes on the latest bar. Codes are documented in markets.js (EVENT_LABELS)."""
    i = len(s["c"]) - 1
    if i < 1:
        return []
    o, h, l, c, v = s["o"], s["h"], s["l"], s["c"], s["v"]
    ev: list[str] = []
    if i >= WEEK52 and h[i] > max(h[i - WEEK52 + 1:i]):
        ev.append("h52")
    if i >= WEEK52 and l[i] < min(l[i - WEEK52 + 1:i]):
        ev.append("l52")
    if i >= 20 and c[i] > max(h[i - 20:i]):
        ev.append("b20")
    if i >= 20 and c[i] < min(l[i - 20:i]):
        ev.append("d20")
    if any(crossed(s["s50"], s["s200"], j, True) for j in range(max(1, i - CROSS_LOOKBACK + 1), i + 1)):
        ev.append("gx")
    if any(crossed(s["s50"], s["s200"], j, False) for j in range(max(1, i - CROSS_LOOKBACK + 1), i + 1)):
        ev.append("dx")
    if crossed(s["macd"], s["macds"], i, True):
        ev.append("macd_up")
    if crossed(s["macd"], s["macds"], i, False):
        ev.append("macd_dn")
    if crossed(c, s["e20"], i, True):
        ev.append("e20_up")
    if crossed(c, s["e20"], i, False):
        ev.append("e20_dn")
    if crossed(c, s["vw20"], i, True):
        ev.append("vw20_up")
    if crossed(c, s["vw20"], i, False):
        ev.append("vw20_dn")
    if finite(s["avgv"][i]) and s["avgv"][i] > 0 and v[i] / s["avgv"][i] >= 3:
        ev.append("vol3")
    if i >= 6 and (h[i] - l[i]) <= min(h[j] - l[j] for j in range(i - 6, i)):
        ev.append("nr7")
    if h[i] < h[i - 1] and l[i] > l[i - 1]:
        ev.append("inside")
    if c[i - 1] < o[i - 1] and c[i] > o[i] and o[i] <= c[i - 1] and c[i] >= o[i - 1]:
        ev.append("bull_engulf")
    if c[i - 1] > o[i - 1] and c[i] < o[i] and o[i] >= c[i - 1] and c[i] <= o[i - 1]:
        ev.append("bear_engulf")
    if o[i] > h[i - 1] and o[i] >= c[i - 1] * 1.02:
        ev.append("gap_up")
    if o[i] < l[i - 1] and o[i] <= c[i - 1] * 0.98:
        ev.append("gap_dn")
    return ev


def stock_key(symbol: str, used: set[str]) -> str:
    key = SAFE_KEY.sub("_", symbol.upper()) or "SYMBOL"
    base, n = key, 2
    while key in used:
        key, n = f"{base}_{n}", n + 1
    used.add(key)
    return key


def research_evidence(history: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Backtest every scan over the multi-year archive plus current history, one symbol at a time."""
    import archive_nse
    data = archive_nse.load_by_symbol(history)
    bench = data.get(BENCHMARK, {})
    bench_close = {d: v[3] for d, v in bench.items()}
    if len(bench_close) < 60:
        return None
    bt = conv.Backtest(bench_close)
    symbols = 0
    for symbol in sorted(data):
        if symbol.startswith("NIFTY") or symbol == "DEMO_INDEX":
            continue
        bars = data[symbol]
        if len(bars) <= conv.WARMUP + 1:
            continue
        rows = [{"date": d, "symbol": symbol, "open": b[0], "high": b[1], "low": b[2], "close": b[3],
                 "volume": b[4], "value": b[5]} for d, b in sorted(bars.items())]
        bt.add(conv.extend(series(rows), bench_close))
        symbols += 1
    out = bt.result()
    out["symbols"] = symbols
    out["source"] = "data/archive monthly files + data/history.csv"
    return out


def add_stockedge_scans(rows: list[list[Any]], all_series: dict[str, dict], bench_close: dict[str, float]) -> int:
    """Fill se (hit IDs), seb (bullish count) and ser (bearish count) for current, non-stale stocks."""
    col = {k: SNAPSHOT_COLUMNS.index(k) for k in ("s", "stale", "ind", "pe", "pb", "dy", "roe", "roce", "de", "se", "seb", "ser")}
    ind_pe: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        if r[col["pe"]] is not None and r[col["pe"]] > 0:
            ind_pe[r[col["ind"]]].append(r[col["pe"]])
    medians = {k: sorted(v)[len(v) // 2] for k, v in ind_pe.items() if len(v) >= 3}
    n = 0
    for r in rows:
        if r[col["stale"]]:
            continue
        fund = {k: r[col[k]] for k in ("pe", "pb", "dy", "roe", "roce", "de")}
        fund = {**fund, "ind_pe": medians.get(r[col["ind"]])} if any(v is not None for v in fund.values()) else None
        hits = se.evaluate(all_series[r[col["s"]]], bench_close, fund)
        rt = se.rating(hits)
        r[col["se"]], r[col["seb"]], r[col["ser"]] = hits, rt["bull"], rt["bear"]
        n += 1
    return n


def regime_today(bench_close: dict[str, float]) -> dict[str, Any] | None:
    """Market regime gate on the latest benchmark date: NIFTY 50 vs its 50-session SMA."""
    dates = sorted(bench_close)
    if len(dates) < 50:
        return None
    sma = sum(bench_close[d] for d in dates[-50:]) / 50
    close = bench_close[dates[-1]]
    return {"date": dates[-1], "on": close > sma, "nifty": rnd(close), "sma50": rnd(sma),
            "pct_vs_sma50": rnd((close / sma - 1) * 100)}


def conviction_today(s: dict[str, list], i: int, has_jump: bool, current: bool) -> dict[str, Any]:
    """Conviction score, passed factors and matching high-conviction scans on the latest bar."""
    if "adx" not in s or i < 1:
        return {"cv": None, "cvf": [], "hc": [], "adx": None, "rs55": None}
    f = conv.factors_at(s, i)
    score = conv.score(f)
    hc = []
    if current and not has_jump and i >= conv.WARMUP and conv.eligible(s, i, conv.HC_MIN_TURNOVER):
        hc = [sc["id"] for sc in conv.HC_SCANS if sc["test"](s, i, f, score, math.nan)]
    return {"cv": score if i >= conv.WARMUP else None, "cvf": [k for k, v in f.items() if v], "hc": hc,
            "adx": rnd(s["adx"][i], 1), "rs55": rnd(s["rs55"][i] * 100 if conv.finite(s["rs55"][i]) else None, 1)}


def snapshot_row(symbol: str, s: dict[str, list[float]], info: dict[str, Any], jumps: list[dict[str, Any]],
                 latest_date: str, key: str, fvals: dict[str, Any] | None = None) -> list[Any]:
    i = len(s["c"]) - 1
    c = s["c"]
    dates = s["date"]
    jump_dates = {j["date"] for j in jumps}
    prev = c[i - 1] if i else math.nan
    def ret(n: int) -> float | None:
        if i < n or c[i - n] <= 0 or window_has_jump(dates, jump_dates, i - n):
            return None
        return rnd((c[i] / c[i - n] - 1) * 100)
    year_ok = not window_has_jump(dates, jump_dates, max(0, i - WEEK52 + 1) - 1)
    h52, l52 = (s["h52"][i], s["l52"][i]) if year_ok else (math.nan, math.nan)
    avg_t = s["avgt"][i]
    row = {
        "s": symbol, "n": info.get("name") or "", "ind": info.get("industry") or "Unclassified",
        "cap": info.get("cap") or "", "n50": 1 if info.get("nifty50") else 0, "d": dates[i],
        "o": rnd(s["o"][i]), "h": rnd(s["h"][i]), "l": rnd(s["l"][i]), "c": rnd(c[i]),
        "ch": rnd((c[i] / prev - 1) * 100) if finite(prev) and prev > 0 and dates[i] not in jump_dates else None,
        "v": int(s["v"][i]), "t": rnd(c[i] * s["v"][i] / 1e7, 3), "at": rnd(avg_t / 1e7, 3),
        "rv": rnd(s["v"][i] / s["avgv"][i]) if finite(s["avgv"][i]) and s["avgv"][i] > 0 else None,
        **{k: ret(n) for k, n in RETURN_WINDOWS.items()},
        "h52": rnd(h52), "l52": rnd(l52),
        "dh": rnd((c[i] / h52 - 1) * 100) if finite(h52) and h52 > 0 else None,
        "dl": rnd((c[i] / l52 - 1) * 100) if finite(l52) and l52 > 0 else None,
        "s20": rnd(s["s20"][i]), "s50": rnd(s["s50"][i]), "s200": rnd(s["s200"][i]), "e20": rnd(s["e20"][i]),
        "rsi": rnd(s["rsi"][i], 1), "atrp": rnd(s["atr"][i] / c[i] * 100) if c[i] > 0 else None,
        "macd": rnd(s["macd"][i], 3), "macds": rnd(s["macds"][i], 3), "macdh": rnd(s["macd"][i] - s["macds"][i], 3),
        "gap": rnd((s["o"][i] / prev - 1) * 100) if finite(prev) and prev > 0 else None,
        "ev": events_for(s), "ca": jumps[-1]["date"] if jumps else None,
        "stale": 1 if dates[i] != latest_date else 0, "key": key, "len": len(c),
        "vw": rnd(s["vw"][i]), "vw20": rnd(s["vw20"][i]),
        **conviction_today(s, i, bool(jumps), dates[i] == latest_date),
        "se": [], "seb": None, "ser": None,  # filled by add_stockedge_scans()
        **{k: None for k in FUND_COLUMNS}, **(fvals or {}),
    }
    return [row[col] for col in SNAPSHOT_COLUMNS]


def breadth(all_series: dict[str, dict[str, list[float]]], jump_flags: dict[str, list[dict[str, Any]]],
            dates: list[str], bench: dict[str, list[float]] | None) -> list[dict[str, Any]]:
    stats = {d: {"adv": 0, "dec": 0, "unch": 0, "a50": 0, "n50": 0, "a200": 0, "n200": 0,
                 "nh": 0, "nl": 0, "n52": 0} for d in dates}
    for symbol, s in all_series.items():
        jumps = {j["date"] for j in jump_flags.get(symbol, [])}
        c, h, l = s["c"], s["h"], s["l"]
        for i, d in enumerate(s["date"]):
            st = stats.get(d)
            if st is None:
                continue
            if i and d not in jumps:
                st["adv" if c[i] > c[i - 1] else "dec" if c[i] < c[i - 1] else "unch"] += 1
            if finite(s["s50"][i]):
                st["n50"] += 1
                st["a50"] += c[i] > s["s50"][i]
            if finite(s["s200"][i]):
                st["n200"] += 1
                st["a200"] += c[i] > s["s200"][i]
            if i >= WEEK52:
                st["n52"] += 1
                st["nh"] += h[i] > max(h[i - WEEK52 + 1:i])
                st["nl"] += l[i] < min(l[i - WEEK52 + 1:i])
    bench_close = dict(zip(bench["date"], bench["c"])) if bench else {}
    out = []
    for d in dates:
        st = stats[d]
        out.append({"d": d, "adv": st["adv"], "dec": st["dec"], "unch": st["unch"],
                    "p50": rnd(st["a50"] / st["n50"] * 100, 1) if st["n50"] else None,
                    "p200": rnd(st["a200"] / st["n200"] * 100, 1) if st["n200"] >= 100 else None,
                    "nh": st["nh"] if st["n52"] >= 100 else None, "nl": st["nl"] if st["n52"] >= 100 else None,
                    "nifty": rnd(bench_close.get(d))})
    return out


def write_compact(path: Path, content: Any) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(content, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    path.write_text(text, encoding="utf-8")
    return len(text.encode("utf-8"))


def build(history: list[dict[str, Any]], constituents: dict[str, dict[str, Any]], out_dir: Path,
          source_note: str, warnings: list[str], equity_list: list[dict[str, str]] | None = None,
          fundamentals: dict[str, dict[str, Any]] | None = None, fundamentals_as_of: str | None = None,
          evidence: dict[str, Any] | None = None) -> dict[str, Any]:
    equity_names = {r["symbol"]: r["name"] for r in (equity_list or [])}
    fundamentals = fundamentals or {}
    fund_blocked: list[str] = []
    by_symbol: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in history:
        by_symbol[row["symbol"]].append(row)
    equity_dates = sorted({r["date"] for r in history if not r["symbol"].startswith("NIFTY")})
    if not equity_dates:
        raise SystemExit("history.csv has no equity rows; run update_nse_data.py first")
    latest = equity_dates[-1]
    jump_flags = core.corporate_action_symbols(by_symbol)

    if out_dir.exists():
        shutil.rmtree(out_dir)
    stocks_dir = out_dir / "stocks"
    used: set[str] = set()
    all_series: dict[str, dict[str, list[float]]] = {}
    rows: list[list[Any]] = []
    stock_bytes = 0
    bench_close = {r["date"]: float(r["close"]) for r in by_symbol.get(BENCHMARK, [])}
    for symbol in sorted(by_symbol):
        if symbol.startswith("NIFTY") or symbol == "DEMO_INDEX":
            continue
        s = conv.extend(series(by_symbol[symbol]), bench_close)
        all_series[symbol] = s
        key = stock_key(symbol, used)
        info = dict(constituents.get(symbol, {}))
        if not info.get("name") and symbol in equity_names:
            info["name"] = equity_names[symbol]
        jumps = jump_flags.get(symbol, [])
        fvals, blocked_by = fundamentals_for(fundamentals.get(symbol), s, jumps, fundamentals_as_of)
        if blocked_by:
            fund_blocked.append(f"{symbol} ({blocked_by})")
        rows.append(snapshot_row(symbol, s, info, jumps, latest, key, fvals))
        bars = [[d, rnd(o), rnd(h), rnd(l), rnd(c), int(v), int(val) if val else None]
                for d, o, h, l, c, v, val in zip(s["date"], s["o"], s["h"], s["l"], s["c"], s["v"], s["val"])]
        stock_bytes += write_compact(stocks_dir / f"{key}.json", {"s": symbol, "bars": bars})

    bench_rows = by_symbol.get(BENCHMARK, [])
    bench = series(bench_rows) if bench_rows else None
    if bench:
        stock_bytes += write_compact(stocks_dir / f"{BENCHMARK}.json", {"s": BENCHMARK, "bars": [
            [d, rnd(o), rnd(h), rnd(l), rnd(c), int(v)]
            for d, o, h, l, c, v in zip(bench["date"], bench["o"], bench["h"], bench["l"], bench["c"], bench["v"])]})
    else:
        warnings.append("NIFTY 50 history is missing; benchmark comparisons are unavailable.")

    i = len(bench["c"]) - 1 if bench else -1
    benchmark = None
    if bench and i >= 1:
        bc = bench["c"]
        benchmark = {"s": BENCHMARK, "d": bench["date"][i], "c": rnd(bc[i]),
                     "ch": rnd((bc[i] / bc[i - 1] - 1) * 100),
                     **{k: rnd((bc[i] / bc[i - n] - 1) * 100) if i >= n else None for k, n in RETURN_WINDOWS.items()},
                     "s50": rnd(bench["s50"][i]), "s200": rnd(bench["s200"][i]), "rsi": rnd(bench["rsi"][i], 1)}
    classified = sum(1 for r in rows if r[SNAPSHOT_COLUMNS.index("cap")])
    se_count = add_stockedge_scans(rows, all_series, bench_close)
    fa_idx, pe_idx, mc_idx = (SNAPSHOT_COLUMNS.index(k) for k in ("fa", "pe", "mcap"))
    fund_dates = sorted({r[fa_idx] for r in rows if r[fa_idx]})
    if fund_blocked:
        warnings.append(f"Fundamentals blanked for {len(fund_blocked)} stock(s) with a >=40% price jump after the "
                        f"export date (likely split/bonus; re-export to refresh): {', '.join(fund_blocked[:10])}"
                        f"{' …' if len(fund_blocked) > 10 else ''}.")
    meta_fund = {
        "available": bool(fundamentals),
        "stocks": sum(1 for r in rows if r[pe_idx] is not None or r[mc_idx] is not None),
        "as_of_min": fund_dates[0] if fund_dates else None, "as_of_max": fund_dates[-1] if fund_dates else None,
        "source": "data/fundamentals.csv (user export); P/E, market cap, P/B and yield recomputed at each close",
    }
    if evidence is None:
        evidence = conv.backtest(all_series, set(jump_flags), equity_dates, bench_close) if bench_close else None
    meta = {
        "stockedge_scans": {"catalogue": se.catalogue(), "evaluated_stocks": se_count,
                            "source": "web.stockedge.com/sitemap/sitemap_scan.xml (510 scans); definitions re-implemented on NSE "
                                      "bhavcopy data with standard parameters; thresholds are ours where StockEdge's are unpublished"},
        "conviction": {"regime": regime_today(bench_close),
                       "factors": [{"id": k, "label": v} for k, v in conv.FACTORS],
                       "scans": [{k: sc[k] for k in ("id", "name", "desc")} for sc in conv.HC_SCANS],
                       "min_turnover_cr": conv.HC_MIN_TURNOVER / 1e7, "evidence": evidence},
        "fundamentals": meta_fund,
        "schema_version": 1,
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "latest_market_date": latest, "history_start": equity_dates[0], "sessions": len(equity_dates),
        "symbols": len(rows), "classified_symbols": classified,
        "stale_symbols": sum(1 for r in rows if r[SNAPSHOT_COLUMNS.index("stale")]),
        "corporate_action_flagged": len(jump_flags),
        "classification_source": source_note,
        "price_source": "NSE CM-UDiFF Common Bhavcopy (EQ series), unadjusted",
        "warnings": warnings + [
            "Prices are raw end-of-day bhavcopy values and are not adjusted for splits, bonuses or dividends. "
            "Returns and 52-week ranges spanning a flagged >=40% one-day move are left blank.",
            "Industry and size bucket cover NSE Nifty 100/Midcap 150/Smallcap 250/Microcap 250 members only; "
            "other listings are 'Unclassified'.",
        ],
    }
    snapshot_bytes = write_compact(out_dir / "snapshot.json",
                                   {"meta": meta, "benchmark": benchmark, "columns": SNAPSHOT_COLUMNS, "rows": rows})
    breadth_bytes = write_compact(out_dir / "breadth.json",
                                  {"latest_market_date": latest, "rows": breadth(all_series, jump_flags, equity_dates, bench)})
    return {**meta, "bytes": {"snapshot": snapshot_bytes, "breadth": breadth_bytes, "stocks": stock_bytes}}


def main(argv: list[str]) -> int:
    out_dir = Path(argv[1]).resolve() if len(argv) > 1 else DEFAULT_OUT
    history = core.load_history()
    if not history:
        print("data/history.csv is empty; nothing to build.", file=sys.stderr)
        return 1
    constituents, source, note = fetch_constituents()
    warnings = [note] if note else []
    equity_list, eq_note = fetch_equity_list()
    if eq_note:
        warnings.append(eq_note)
    known = {r["symbol"] for r in history if not r["symbol"].startswith("NIFTY")}
    fundamentals, fund_warnings, fund_as_of = load_fundamentals(known, equity_list, constituents)
    warnings += fund_warnings
    evidence = research_evidence(history)
    summary = build(history, constituents, out_dir, source, warnings, equity_list, fundamentals, fund_as_of, evidence)
    print(json.dumps({k: summary[k] for k in ("latest_market_date", "sessions", "symbols", "classified_symbols",
                                              "stale_symbols", "classification_source", "fundamentals", "bytes")}, indent=2))
    annotate_screen(out_dir / "snapshot.json", core.SCREEN_PATH)
    for w in warnings:
        print("WARNING:", w, file=sys.stderr)
    return 0


def annotate_screen(snapshot_path: Path, screen_path: Path) -> int:
    """Copy each screener candidate's conviction score and factors into latest_screen.json."""
    if not snapshot_path.exists() or not screen_path.exists():
        return 0
    snap = json.loads(snapshot_path.read_text(encoding="utf-8"))
    cols = snap["columns"]
    by = {r[0]: dict(zip(cols, r)) for r in snap["rows"]}
    se_names = {c["id"]: c for c in snap["meta"].get("stockedge_scans", {}).get("catalogue", [])}
    screen = json.loads(screen_path.read_text(encoding="utf-8"))
    n = 0
    for cand in screen.get("candidates", []):
        row = by.get(cand.get("symbol"))
        if row and row["d"] == cand.get("date"):
            cand["conviction"], cand["conviction_factors"], cand["hc_scans"] = row["cv"], row["cvf"], row["hc"]
            cand["se_bull"], cand["se_bear"] = row.get("seb"), row.get("ser")
            cand["se_hits"] = [[h, se_names[h]["name"], se_names[h]["bias"]] for h in row.get("se") or [] if h in se_names]
            n += 1
    cmeta = snap["meta"]["conviction"]
    screen["conviction_factors"] = cmeta["factors"]
    screen["market_regime"] = cmeta.get("regime")
    ev = cmeta.get("evidence") or {}
    setup_ids = {"breakout": "scr_breakout", "pullback": "scr_pullback", "relative_strength": "scr_rs"}
    screen["setup_evidence"] = {k: {**ev["scans"][v]["h10"], "trade": ev["scans"][v].get("trade"),
                                    "by_year_on": ev["scans"][v].get("by_year_on"), "verdict": ev["scans"][v].get("verdict")}
                                for k, v in setup_ids.items() if v in ev.get("scans", {})}
    screen["setup_evidence_period"] = ev.get("period")
    core.write_json(screen_path, screen, compact=True)
    return n


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
