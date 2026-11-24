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
from array import array
from collections import defaultdict
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
    udr = [math.nan] * n
    su = sd = 0.0
    for i in range(n):
        su += up[i]; sd += dn[i]
        if i >= 20:
            su -= up[i - 20]; sd -= dn[i - 20]
            udr[i] = su / sd if sd > 0 else math.nan
    s["udr"] = udr
    s["hmax"] = s["h52"] if "h52" in s else [max(h[max(0, i - 249):i + 1]) for i in range(n)]
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


# ----------------------------------------------------------------------------- trade simulation
# The proposal rules the Stock scanner turns signals into (see README "Trade proposals").
ENTRY_PAD, ENTRY_WINDOW, STOP_ATR, TARGET_R, MAX_HOLD = 0.0025, 3, 2.0, 2.0, 10
ROUND_TRIP_COST = 0.0025   # 0.15% charges + 0.10% slippage
JUMP_LOOKBACK, JUMP_LOOKAHEAD = 250, 15


def simulate_trade(s: dict[str, list], i: int) -> tuple[str, float | None]:
    """('trade', R) | ('expired', None) | ('open', None) for a signal on bar i.
    Entry: stop-entry 0.25% above the signal close within 3 sessions (a gap fills at the open).
    Exit: stop at close - 2*ATR(14) or target at 2R, stop assumed first when both touch in one bar;
    otherwise the close after 10 sessions. R is net of costs."""
    n = len(s["c"])
    c, atr = s["c"][i], s["atr"][i]
    if not finite(atr) or atr <= 0:
        return "expired", None
    trig, stop = c * (1 + ENTRY_PAD), c - STOP_ATR * atr
    entry = None
    for j in range(i + 1, min(i + 1 + ENTRY_WINDOW, n)):
        if s["h"][j] >= trig:
            entry = (j, max(trig, s["o"][j]))
            break
    if entry is None:
        return ("expired", None) if i + ENTRY_WINDOW < n else ("open", None)
    j0, ep = entry
    risk = ep - stop
    if risk <= 0:
        return "expired", None
    target = ep + TARGET_R * risk
    exit_px = None
    for j in range(j0, min(j0 + MAX_HOLD, n)):
        if s["l"][j] <= stop:
            exit_px = min(stop, s["o"][j]) if j > j0 else stop
            break
        if s["h"][j] >= target:
            exit_px = max(target, s["o"][j]) if j > j0 else target
            break
    if exit_px is None:
        if j0 + MAX_HOLD > n:
            return "open", None
        exit_px = s["c"][j0 + MAX_HOLD - 1]
    return "trade", (exit_px / ep - 1 - ROUND_TRIP_COST) / (risk / ep)


def jump_indices(s: dict[str, list]) -> list[int]:
    c = s["c"]
    return [j for j in range(1, len(c)) if c[j - 1] > 0 and abs(c[j] / c[j - 1] - 1) >= 0.4]


class Backtest:
    """Streaming event study: add one symbol's series at a time, then call result().

    For each scan, a signal is a stock newly entering it. Two measurements:
      * forward return: next session's open to the close after 5/10 sessions, compared with
        the average eligible stock on the same date ("excess");
      * trade simulation of the proposal rules, in R net of costs.
    Signals and universe returns within 250 sessions before / 15 after a >=40% one-day move
    are skipped (unadjusted splits/bonuses would fake returns); the rest of that stock is kept.
    """

    def __init__(self, bench_close: dict[str, float]):
        self.scans = HC_SCANS + SCREENER_RULES
        self.regime = market_regime(bench_close)
        bdates = sorted(bench_close)
        self.bmom = {d: (bench_close[d] / bench_close[bdates[k - 60]] - 1) if k >= 60 else math.nan
                     for k, d in enumerate(bdates)}
        self.uni_sum = {hz: defaultdict(float) for hz in HORIZONS}
        self.uni_cnt = {hz: defaultdict(int) for hz in HORIZONS}
        self.uni_all = {hz: array("d") for hz in HORIZONS}
        self.uni_dates = {hz: [] for hz in HORIZONS}
        # per scan: list of (date, {hz: fwd}, outcome, R)
        self.events: dict[str, list[tuple[str, dict[int, float], str, float | None]]] = {sc["id"]: [] for sc in self.scans}
        self.eval_dates: set[str] = set()

    def add(self, s: dict[str, list]) -> None:
        n = len(s["c"])
        jumps = jump_indices(s)
        blocked = [False] * n
        for j in jumps:
            for k in range(max(0, j - JUMP_LOOKAHEAD), min(n, j + JUMP_LOOKBACK + 1)):
                blocked[k] = True
        prev = {sc["id"]: False for sc in self.scans}
        for i in range(WARMUP, n - 1):
            d = s["date"][i]
            if blocked[i] or d not in self.regime or not eligible(s, i, BASE_MIN_TURNOVER):
                prev = dict.fromkeys(prev, False)
                continue
            fwd = {}
            for hz in HORIZONS:
                if i + hz < n and s["o"][i + 1] > 0:
                    r = s["c"][i + hz] / s["o"][i + 1] - 1
                    fwd[hz] = r
                    self.uni_sum[hz][d] += r
                    self.uni_cnt[hz][d] += 1
                    self.uni_all[hz].append(r)
                    self.uni_dates[hz].append(d)
            if fwd:
                self.eval_dates.add(d)
            f = factors_at(s, i)
            sc_ = score(f)
            bm = self.bmom.get(d, math.nan)
            for scan in self.scans:
                floor = HC_MIN_TURNOVER if scan["id"].startswith("hc_") else BASE_MIN_TURNOVER
                hit = s["avgt"][i] >= floor and bool(scan["test"](s, i, f, sc_, bm))
                if hit and not prev[scan["id"]] and fwd:
                    outcome, r_mult = simulate_trade(s, i)
                    self.events[scan["id"]].append((d, fwd, outcome, r_mult))
                prev[scan["id"]] = hit

    def _fwd_stats(self, ev, hz) -> dict[str, Any]:
        pairs = [(d, f[hz]) for d, f, _, _ in ev if hz in f]
        out = _summary([r for _, r in pairs],
                       [r - self.uni_sum[hz][d] / self.uni_cnt[hz][d] for d, r in pairs if self.uni_cnt[hz][d]])
        out["days"] = len({d for d, _ in pairs})
        return out

    @staticmethod
    def _trade_stats(ev) -> dict[str, Any]:
        trades = [(d, r) for d, _, o, r in ev if o == "trade"]
        expired = sum(1 for _, _, o, _ in ev if o == "expired")
        if not trades:
            return {"trades": 0, "expired": expired}
        rs = [r for _, r in trades]
        by_day: dict[str, list[float]] = defaultdict(list)
        for d, r in trades:
            by_day[d].append(r)
        day_r = [statistics.fmean(v) for v in by_day.values()]
        t = None
        if len(day_r) >= 3 and statistics.pstdev(day_r) > 0:
            t = statistics.fmean(day_r) / (statistics.stdev(day_r) / math.sqrt(len(day_r)))
        return {"trades": len(rs), "expired": expired, "days": len(day_r),
                "win": round(sum(1 for r in rs if r > 0) / len(rs) * 100, 1),
                "avg_r": round(statistics.fmean(rs), 3), "median_r": round(statistics.median(rs), 2),
                "days_positive": round(sum(1 for r in day_r if r > 0) / len(day_r) * 100, 1),
                "t_days": round(t, 2) if t is not None else None}

    def _group(self, ev) -> dict[str, Any]:
        return {**{f"h{hz}": self._fwd_stats(ev, hz) for hz in HORIZONS}, "trade": self._trade_stats(ev)}

    def result(self) -> dict[str, Any]:
        dates = sorted(self.eval_dates)
        out: dict[str, Any] = {
            "period": [dates[0], dates[-1]] if dates else [None, None], "horizons": list(HORIZONS),
            "method": ("New entries only. Forward return: next session's open to the close after N sessions; excess = "
                       "minus the average eligible stock that date. Trade: proposal rules (stop-entry +0.25% within 3 "
                       "sessions, stop close-2xATR, target 2R, max 10 sessions, stop-first), 0.25% round-trip costs, "
                       "in R. t = mean of per-day average R / standard error across signal days. Windows around a >=40% "
                       "one-day move (likely split/bonus) are skipped."),
            "regime_days": {"on": sum(1 for d in dates if self.regime.get(d)), "off": sum(1 for d in dates if self.regime.get(d) is False)},
            "scans": {},
        }
        for scan in self.scans:
            ev = self.events[scan["id"]]
            on = [e for e in ev if self.regime.get(e[0])]
            off = [e for e in ev if self.regime.get(e[0]) is False]
            years = sorted({e[0][:4] for e in on})
            entry = {"name": scan["name"]}
            for key, group in (("all", ev), ("on", on), ("off", off)):
                g = self._group(group)
                for k, v in g.items():
                    entry.setdefault(k, {})[key] = v
            entry["by_year_on"] = {y: self._trade_stats([e for e in on if e[0][:4] == y]) for y in years}
            entry["verdict"] = verdict(entry)
            out["scans"][scan["id"]] = entry
        base: dict[str, Any] = {}
        for hz in HORIZONS:
            groups = {"all": list(range(len(self.uni_all[hz])))}
            groups["on"] = [k for k, d in enumerate(self.uni_dates[hz]) if self.regime.get(d)]
            groups["off"] = [k for k, d in enumerate(self.uni_dates[hz]) if self.regime.get(d) is False]
            base[f"h{hz}"] = {}
            for key, idx in groups.items():
                vals = [self.uni_all[hz][k] for k in idx]
                base[f"h{hz}"][key] = {**_summary(vals, [0.0] * len(vals)),
                                       "days": len({self.uni_dates[hz][k] for k in idx})}
        out["baseline"] = base
        return out


def backtest(all_series: dict[str, dict], jump_symbols: set[str], dates: list[str],
             bench_close: dict[str, float]) -> dict[str, Any]:
    """Convenience wrapper over Backtest for in-memory series (jump_symbols is ignored:
    split/bonus windows are detected per series)."""
    bt = Backtest(bench_close)
    for s in all_series.values():
        bt.add(s)
    return bt.result()


def verdict(stats: dict[str, Any], min_days: int = 30) -> str:
    """Judged on the regime-on trade simulation, with signal days as the unit of evidence.
    supported: >= min_days signal days, positive average R, t >= 2 and a positive 10-day excess.
    weak: positive average R with t >= 1. insufficient: fewer than min_days signal days."""
    t = stats.get("trade", {}).get("on", {})
    if (t.get("days") or 0) < min_days:
        return "insufficient"
    avg, tval = t.get("avg_r") or 0, t.get("t_days") or 0
    excess = (stats.get("h10", {}).get("on", {}) or {}).get("excess") or 0
    if avg > 0 and tval >= 2 and excess > 0:
        return "supported"
    if avg > 0 and tval >= 1:
        return "weak"
    return "not_supported"
