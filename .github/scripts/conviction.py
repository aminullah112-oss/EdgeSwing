"""Conviction scoring, high-conviction scans and their historical evidence.

Single-condition scans (one indicator crossing a level) fire on many stocks a day and
carry little information on their own. Here a stock earns one point per independent
confirmation on a bar:

    trend       close > SMA50 > SMA200 and SMA200 higher than 20 sessions ago
    rs55        outperformed NIFTY 50 over the last 55 sessions
    momentum    RSI(14) between 50 and 70 and MACD above its signal line
    vwap        close above the 20-session VWAP (exact where traded value exists,
                typical-price approximation for older history)
    adx         ADX(14) >= 20: a trending market, not chop
    leader      close within 15% of the highest high of the last 250 sessions
    volume      up-day volume exceeds down-day volume over 20 sessions (accumulation)

High-conviction scans combine the score with a specific trigger. Every scan, plus the
existing screener setups and a whole-universe baseline, is replayed over stored history:
a signal is a stock newly entering a scan, entry is the next session's open and exit is
the close 5 or 10 sessions later. Results are reported against the average eligible
stock on the same dates, so a scan only looks good if it beat simply holding everything.
"""
from __future__ import annotations

import math
import statistics
from typing import Any, Callable

FACTORS = [
    ("trend", "Uptrend: close > SMA50 > SMA200, SMA200 rising"),
    ("rs55", "Beating NIFTY 50 over 55 sessions"),
    ("momentum", "RSI 50–70 with MACD above signal"),
    ("vwap", "Close above 20-session VWAP"),
    ("adx", "ADX(14) ≥ 20 (trending)"),
    ("leader", "Within 15% of 52-week high"),
    ("volume", "Up-day volume > down-day volume (20 sessions)"),
]
HC_MIN_TURNOVER = 5e7    # ₹5 crore 20-session average turnover for high-conviction scans
BASE_MIN_TURNOVER = 1e7  # ₹1 crore, the screener's liquidity floor
HORIZONS = (5, 10)
WARMUP = 200             # SMA200 must exist


def finite(x: Any) -> bool:
    return isinstance(x, (int, float)) and math.isfinite(x)


def wilder_adx(h: list[float], l: list[float], c: list[float], n: int = 14) -> list[float]:
    size = len(c)
    out = [math.nan] * size
    if size <= 2 * n:
        return out
    tr, pdm, ndm = [0.0] * size, [0.0] * size, [0.0] * size
    for i in range(1, size):
        up, down = h[i] - h[i - 1], l[i - 1] - l[i]
        pdm[i] = up if up > down and up > 0 else 0.0
        ndm[i] = down if down > up and down > 0 else 0.0
        tr[i] = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
    atr, sp, sn = sum(tr[1:n + 1]), sum(pdm[1:n + 1]), sum(ndm[1:n + 1])
    dx: list[float] = []
    for i in range(n, size):
        if i > n:
            atr = atr - atr / n + tr[i]
            sp = sp - sp / n + pdm[i]
            sn = sn - sn / n + ndm[i]
        pdi = 100 * sp / atr if atr else 0.0
        ndi = 100 * sn / atr if atr else 0.0
        dx.append(100 * abs(pdi - ndi) / (pdi + ndi) if pdi + ndi else 0.0)
        k = len(dx)
        if k == n:
            out[i] = sum(dx) / n
        elif k > n:
            out[i] = (out[i - 1] * (n - 1) + dx[-1]) / n
    return out


def extend(s: dict[str, list], bench_close: dict[str, float]) -> dict[str, list]:
    """Add the series conviction needs to a build_market_data.series() dict (in place)."""
    o, h, l, c, v, val = s["o"], s["h"], s["l"], s["c"], s["v"], s["val"]
    n = len(c)
    # VWAP20 with typical-price fallback where exact traded value is missing.
    pv = [val[i] if val[i] else (h[i] + l[i] + c[i]) / 3 * v[i] for i in range(n)]
    vw = [math.nan] * n
    spv = sv = 0.0
    for i in range(n):
        spv += pv[i]; sv += v[i]
        if i >= 20:
            spv -= pv[i - 20]; sv -= v[i - 20]
        if i >= 19 and sv > 0:
            vw[i] = spv / sv
    s["vw20x"] = vw
    s["adx"] = wilder_adx(h, l, c)
    bc = [bench_close.get(d, math.nan) for d in s["date"]]
    s["rs55"] = [(c[i] / c[i - 55]) / (bc[i] / bc[i - 55]) - 1
                 if i >= 55 and c[i - 55] > 0 and finite(bc[i]) and finite(bc[i - 55]) and bc[i - 55] > 0 else math.nan
                 for i in range(n)]
    up = [v[i] if i and c[i] > c[i - 1] else 0.0 for i in range(n)]
    dn = [v[i] if i and c[i] < c[i - 1] else 0.0 for i in range(n)]
    s["udr"] = [(sum(up[i - 19:i + 1]) / sum(dn[i - 19:i + 1])) if i >= 20 and sum(dn[i - 19:i + 1]) > 0 else math.nan
                for i in range(n)]
    s["hmax"] = [max(h[max(0, i - 249):i + 1]) for i in range(n)]
    return s


def factors_at(s: dict[str, list], i: int) -> dict[str, bool]:
    c = s["c"][i]
    s50, s200 = s["s50"][i], s["s200"][i]
    rsi, adx = s["rsi"][i], s["adx"][i]
    return {
        "trend": finite(s200) and i >= 20 and finite(s["s200"][i - 20]) and c > s50 > s200 and s200 > s["s200"][i - 20],
        "rs55": finite(s["rs55"][i]) and s["rs55"][i] > 0,
        "momentum": finite(rsi) and 50 <= rsi <= 70 and s["macd"][i] > s["macds"][i],
        "vwap": finite(s["vw20x"][i]) and c > s["vw20x"][i],
        "adx": finite(adx) and adx >= 20,
        "leader": i >= WARMUP and c >= 0.85 * s["hmax"][i],
        "volume": finite(s["udr"][i]) and s["udr"][i] > 1.0,
    }


def score(f: dict[str, bool]) -> int:
    return sum(1 for v in f.values() if v)


def eligible(s: dict[str, list], i: int, floor: float) -> bool:
    return i >= WARMUP and finite(s["s200"][i]) and finite(s["avgt"][i]) and s["avgt"][i] >= floor


def _rv(s, i):
    a = s["avgv"][i]
    return s["v"][i] / a if finite(a) and a > 0 else math.nan


def _strong_close(s, i):
    rng = s["h"][i] - s["l"][i]
    return rng > 0 and (s["c"][i] - s["l"][i]) / rng >= 2 / 3


# Each test: (series, index, factors, conviction score, benchmark momentum60 at that date) -> bool
Test = Callable[[dict, int, dict, int, float], bool]
HC_SCANS: list[dict[str, Any]] = [
    {"id": "hc_leaders", "name": "Conviction leaders",
     "desc": "At least 6 of 7 conviction factors, including uptrend and beating NIFTY",
     "test": lambda s, i, f, sc, bm: sc >= 6 and f["trend"] and f["rs55"]},
    {"id": "hc_breakout", "name": "High-conviction breakout",
     "desc": "Conviction ≥ 5 with uptrend and RS, close above prior 20-session high on ≥1.5× volume, closing in the top third of the day's range",
     "test": lambda s, i, f, sc, bm: sc >= 5 and f["trend"] and f["rs55"] and s["c"][i] > max(s["h"][i - 20:i])
     and _rv(s, i) >= 1.5 and _strong_close(s, i)},
    {"id": "hc_pullback", "name": "High-conviction pullback",
     "desc": "Uptrend leader beating NIFTY with accumulation volume, RSI 40–55, back within 3% of EMA20 or SMA50, and an up close",
     "test": lambda s, i, f, sc, bm: f["trend"] and f["rs55"] and f["volume"] and 40 <= s["rsi"][i] <= 55
     and (abs(s["c"][i] / s["e20"][i] - 1) <= 0.03 or abs(s["c"][i] / s["s50"][i] - 1) <= 0.03) and s["c"][i] > s["c"][i - 1]},
    {"id": "hc_vwap", "name": "High-conviction VWAP reclaim",
     "desc": "Uptrend leader beating NIFTY closing back above its 20-session VWAP on ≥1.2× volume",
     "test": lambda s, i, f, sc, bm: f["trend"] and f["rs55"] and finite(s["vw20x"][i - 1])
     and s["c"][i - 1] <= s["vw20x"][i - 1] and s["c"][i] > s["vw20x"][i] and _rv(s, i) >= 1.2},
]


def _prior_high20(s, i):
    return max(s["h"][i - 20:i])


# The existing screener rules, replayed for comparison (same definitions as update_nse_data.screen).
SCREENER_RULES: list[dict[str, Any]] = [
    {"id": "scr_breakout", "name": "Screener: breakout",
     "test": lambda s, i, f, sc, bm: s["c"][i] > s["s50"][i] > s["s200"][i] and s["c"][i] > _prior_high20(s, i)
     and _rv(s, i) >= 1.5 and 52 <= s["rsi"][i] <= 72},
    {"id": "scr_pullback", "name": "Screener: pullback",
     "test": lambda s, i, f, sc, bm: s["c"][i] > s["s50"][i] > s["s200"][i] and abs(s["c"][i] - s["e20"][i]) / s["e20"][i] <= .025
     and s["c"][i] > s["e20"][i] and s["c"][i - 1] <= s["e20"][i - 1] and 42 <= s["rsi"][i] <= 62},
    {"id": "scr_rs", "name": "Screener: relative strength",
     "test": lambda s, i, f, sc, bm: finite(bm) and i >= 60 and s["c"][i - 60] > 0 and s["c"][i] / s["c"][i - 60] - 1 > bm
     and s["c"][i] > s["s50"][i] > s["s200"][i] and s["c"][i] > _prior_high20(s, i)},
]


def _summary(rets: list[float], excess: list[float]) -> dict[str, Any]:
    if not rets:
        return {"n": 0}
    return {"n": len(rets), "mean": round(statistics.fmean(rets) * 100, 2), "median": round(statistics.median(rets) * 100, 2),
            "hit": round(sum(1 for r in rets if r > 0) / len(rets) * 100, 1),
            "excess": round(statistics.fmean(excess) * 100, 2) if excess else None}


def market_regime(bench_close: dict[str, float], n: int = 50) -> dict[str, bool]:
    """Date -> True when NIFTY 50 closes above its own n-session SMA (the regime gate)."""
    dates = sorted(bench_close)
    out: dict[str, bool] = {}
    for k, d in enumerate(dates):
        if k + 1 >= n:
            sma = sum(bench_close[x] for x in dates[k - n + 1:k + 1]) / n
            out[d] = bench_close[d] > sma
    return out


def backtest(all_series: dict[str, dict], jump_symbols: set[str], dates: list[str],
             bench_close: dict[str, float]) -> dict[str, Any]:
    """Event study over stored history. Returns per-scan forward-return statistics,
    overall and split by market regime (NIFTY 50 above / below its 50-session SMA)."""
    scans = HC_SCANS + SCREENER_RULES
    regime = market_regime(bench_close)
    bdates = [d for d in dates if d in bench_close]
    bmom = {d: (bench_close[d] / bench_close[bdates[k - 60]] - 1) if k >= 60 else math.nan for k, d in enumerate(bdates)}
    date_idx = {d: k for k, d in enumerate(dates)}
    universe: dict[int, dict[str, list[float]]] = {h: {} for h in HORIZONS}  # date -> fwd returns of eligible stocks
    events: dict[str, dict[int, list[tuple[str, float]]]] = {sc["id"]: {h: [] for h in HORIZONS} for sc in scans}
    first_eval = last_eval = None
    for sym, s in all_series.items():
        if sym in jump_symbols:
            continue  # unadjusted splits/bonuses would fake returns
        n = len(s["c"])
        prev = {sc["id"]: False for sc in scans}
        for i in range(WARMUP, n - 1):
            if s["date"][i] not in date_idx or not eligible(s, i, BASE_MIN_TURNOVER):
                prev = {k: False for k in prev}
                continue
            f = factors_at(s, i)
            sc_ = score(f)
            bm = bmom.get(s["date"][i], math.nan)
            fwd = {}
            for hz in HORIZONS:
                if i + hz < n and s["o"][i + 1] > 0:
                    fwd[hz] = s["c"][i + hz] / s["o"][i + 1] - 1
                    universe[hz].setdefault(s["date"][i], []).append(fwd[hz])
            for scan in scans:
                floor = HC_MIN_TURNOVER if scan["id"].startswith("hc_") else BASE_MIN_TURNOVER
                hit = s["avgt"][i] >= floor and bool(scan["test"](s, i, f, sc_, bm))
                if hit and not prev[scan["id"]]:
                    for hz, r in fwd.items():
                        events[scan["id"]][hz].append((s["date"][i], r))
                    if fwd:
                        first_eval = min(first_eval or s["date"][i], s["date"][i])
                        last_eval = max(last_eval or s["date"][i], s["date"][i])
                prev[scan["id"]] = hit
    uni_mean = {hz: {d: statistics.fmean(v) for d, v in by.items() if v} for hz, by in universe.items()}
    out: dict[str, Any] = {"period": [first_eval, last_eval], "horizons": list(HORIZONS),
                           "method": "New entries only; entry next session's open, exit close after N sessions; "
                                     "excess = return minus the average eligible stock on the same date. "
                                     "Unadjusted prices: stocks with a >=40% one-day jump are excluded.",
                           "scans": {}}
    def split(ev: list[tuple[str, float]], hz: int) -> dict[str, Any]:
        groups = {"all": ev, "on": [e for e in ev if regime.get(e[0])], "off": [e for e in ev if regime.get(e[0]) is False]}
        res = {}
        for key, g in groups.items():
            res[key] = _summary([r for _, r in g], [r - uni_mean[hz][d] for d, r in g if d in uni_mean[hz]])
            res[key]["days"] = len({d for d, _ in g})
        return res

    for scan in scans:
        out["scans"][scan["id"]] = {"name": scan["name"],
                                    **{f"h{hz}": split(events[scan["id"]][hz], hz) for hz in HORIZONS}}
    out["baseline"] = {f"h{hz}": split([(d, r) for d, v in universe[hz].items() for r in v], hz) for hz in HORIZONS}
    eval_days = [d for d in dates[WARMUP:] if d in regime]
    out["regime_days"] = {"on": sum(1 for d in eval_days if regime[d]), "off": sum(1 for d in eval_days if not regime[d])}
    return out


def verdict(stats: dict[str, Any], horizon: int = 10, min_n: int = 30) -> str:
    """Judged on regime-on signals (the way high-conviction scans are meant to be used):
    'supported' only with a meaningful sample, a positive median and a win over the average stock."""
    h = stats.get(f"h{horizon}", {}).get("on", {})
    if h.get("n", 0) < min_n:
        return "insufficient"
    return "supported" if (h.get("excess") or 0) > 0 and h.get("median", 0) > 0 else "not_supported"
