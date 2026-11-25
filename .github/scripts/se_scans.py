"""StockEdge-style technical scan catalogue, computed on our own NSE EOD data.

StockEdge publishes ~510 named scans (web.stockedge.com/sitemap/sitemap_scan.xml). Their names
describe the condition ("close crossing 50 SMA from below", "NR7 breakout"), but the exact
parameters and thresholds are not published. This module re-implements the ones computable from
daily OHLCV plus traded value, with standard indicator settings, and keeps StockEdge's numeric
scan ID so a result can be compared with their page for the same scan.

Not implemented (data we do not have): delivery %, F&O open interest / PCR, shareholding and
pledge, financial-statement and corporate-event scans, sectoral-index comparisons, their
proprietary momentum score and static/adaptive relative strength, and 2-/5-year/all-time
extremes (the live history holds ~320 sessions). Fundamentals scans run only when
data/fundamentals.csv is present.

Each scan has a bias: bullish, bearish or neutral (state descriptions such as "doji" or "RSI in
overbought zone" are neutral). A stock's rating is the count of bullish and bearish hits on the
latest session. A hit count is context, not a forecast: single conditions carry little edge.
"""
from __future__ import annotations

import datetime as dt
import math
from typing import Any, Callable

BULL, BEAR, NEUTRAL = "bullish", "bearish", "neutral"
CATALOGUE: list[dict[str, Any]] = []
_TESTS: dict[int, Callable[[dict, int], bool]] = {}


def scan(sid: int, slug: str, name: str, category: str, bias: str, note: str = ""):
    def deco(fn):
        CATALOGUE.append({"id": sid, "slug": slug, "name": name, "cat": category, "bias": bias, "note": note})
        _TESTS[sid] = fn
        return fn
    return deco


def ok(*xs) -> bool:
    return all(isinstance(x, (int, float)) and math.isfinite(x) for x in xs)


# ----------------------------------------------------------------------------- indicators
def sma(v: list[float], n: int) -> list[float]:
    out, t = [math.nan] * len(v), 0.0
    for i, x in enumerate(v):
        t += x
        if i >= n:
            t -= v[i - n]
        if i >= n - 1:
            out[i] = t / n
    return out


def ema(v: list[float], n: int) -> list[float]:
    k, out = 2 / (n + 1), []
    for i, x in enumerate(v):
        out.append(x if i == 0 else k * x + (1 - k) * out[-1])
    # EMAs are unreliable before n bars
    return [x if i >= n - 1 else math.nan for i, x in enumerate(out)]


def wilder(v: list[float], n: int, start: int = 1) -> list[float]:
    out = [math.nan] * len(v)
    if len(v) < start + n:
        return out
    a = sum(v[start:start + n]) / n
    out[start + n - 1] = a
    for i in range(start + n, len(v)):
        a = (a * (n - 1) + v[i]) / n
        out[i] = a
    return out


def rolling(v: list[float], n: int, fn) -> list[float]:
    return [fn(v[i - n + 1:i + 1]) if i >= n - 1 else math.nan for i in range(len(v))]


def rsi(c: list[float], n: int = 14) -> list[float]:
    g = [0.0] + [max(0.0, c[i] - c[i - 1]) for i in range(1, len(c))]
    l = [0.0] + [max(0.0, c[i - 1] - c[i]) for i in range(1, len(c))]
    ag, al = wilder(g, n), wilder(l, n)
    return [100.0 if ok(a, b) and b == 0 and a > 0 else 50.0 if ok(a, b) and b == 0 else
            100 - 100 / (1 + a / b) if ok(a, b) else math.nan for a, b in zip(ag, al)]


def stochastic(h, l, c, n=14, k_smooth=3, d_smooth=3):
    hh, ll = rolling(h, n, max), rolling(l, n, min)
    raw = [100 * (c[i] - ll[i]) / (hh[i] - ll[i]) if ok(hh[i], ll[i]) and hh[i] > ll[i] else math.nan for i in range(len(c))]
    k = _sma_nan(raw, k_smooth)
    return k, _sma_nan(k, d_smooth)


def _sma_nan(v, n):
    return [sum(v[i - n + 1:i + 1]) / n if i >= n - 1 and all(ok(x) for x in v[i - n + 1:i + 1]) else math.nan for i in range(len(v))]


def psar(h, l, step=0.02, cap=0.2):
    n = len(h)
    out, up = [math.nan] * n, [None] * n
    if n < 3:
        return out, up
    bull, af, ep, sar = h[1] >= h[0], step, (h[1] if h[1] >= h[0] else l[1]), (l[0] if h[1] >= h[0] else h[0])
    for i in range(2, n):
        sar = sar + af * (ep - sar)
        if bull:
            sar = min(sar, l[i - 1], l[i - 2])
            if l[i] < sar:
                bull, sar, ep, af = False, ep, l[i], step
            elif h[i] > ep:
                ep, af = h[i], min(cap, af + step)
        else:
            sar = max(sar, h[i - 1], h[i - 2])
            if h[i] > sar:
                bull, sar, ep, af = True, ep, h[i], step
            elif l[i] < ep:
                ep, af = l[i], min(cap, af + step)
        out[i], up[i] = sar, bull
    return out, up


def supertrend(h, l, c, n=10, mult=3.0):
    tr = [h[0] - l[0]] + [max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])) for i in range(1, len(c))]
    atr = wilder(tr, n, start=0)
    st, up = [math.nan] * len(c), [None] * len(c)
    fu = fl = math.nan
    for i in range(len(c)):
        if not ok(atr[i]):
            continue
        mid = (h[i] + l[i]) / 2
        bu, bl = mid + mult * atr[i], mid - mult * atr[i]
        fu = bu if not ok(fu) or bu < fu or c[i - 1] > fu else fu
        fl = bl if not ok(fl) or bl > fl or c[i - 1] < fl else fl
        prev = up[i - 1] if i else None
        trend = True if prev is None else (c[i] > fu if prev is False else not (c[i] < fl))
        up[i], st[i] = trend, (fl if trend else fu)
    return st, up


def dmi(h, l, c, n=14):
    size = len(c)
    tr, pdm, ndm = [0.0] * size, [0.0] * size, [0.0] * size
    for i in range(1, size):
        a, b = h[i] - h[i - 1], l[i - 1] - l[i]
        pdm[i] = a if a > b and a > 0 else 0.0
        ndm[i] = b if b > a and b > 0 else 0.0
        tr[i] = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
    atr, sp, sn = wilder(tr, n), wilder(pdm, n), wilder(ndm, n)
    pdi = [100 * p / a if ok(p, a) and a else math.nan for p, a in zip(sp, atr)]
    ndi = [100 * q / a if ok(q, a) and a else math.nan for q, a in zip(sn, atr)]
    dx = [100 * abs(p - q) / (p + q) if ok(p, q) and p + q else math.nan for p, q in zip(pdi, ndi)]
    first = next((i for i, x in enumerate(dx) if ok(x)), None)
    adx = [math.nan] * size
    if first is not None and first + n <= size:
        a = sum(dx[first:first + n]) / n
        adx[first + n - 1] = a
        for i in range(first + n, size):
            a = (a * (n - 1) + dx[i]) / n
            adx[i] = a
    return adx, pdi, ndi, atr


def weekly(dates: list[str], o, h, l, c, v):
    """Aggregate daily bars into ISO weeks (the current week is partial)."""
    keys, W = [], {"o": [], "h": [], "l": [], "c": [], "v": []}
    for i, d in enumerate(dates):
        y, w, _ = dt.date.fromisoformat(d).isocalendar()
        if not keys or keys[-1] != (y, w):
            keys.append((y, w))
            W["o"].append(o[i]); W["h"].append(h[i]); W["l"].append(l[i]); W["c"].append(c[i]); W["v"].append(v[i])
        else:
            W["h"][-1] = max(W["h"][-1], h[i]); W["l"][-1] = min(W["l"][-1], l[i])
            W["c"][-1] = c[i]; W["v"][-1] += v[i]
    return keys, W


def compute(s: dict[str, list], bench_close: dict[str, float]) -> dict[str, Any]:
    """Indicators for one stock. `s` is a build_market_data.series() dict."""
    d, o, h, l, c, v = s["date"], s["o"], s["h"], s["l"], s["c"], s["v"]
    n = len(c)
    x: dict[str, Any] = dict(s)
    for p in (5, 20, 50, 100, 200):
        x[f"sma{p}"], x[f"ema{p}"] = sma(c, p), ema(c, p)
    x["rsi"] = rsi(c)
    tp = [(h[i] + l[i] + c[i]) / 3 for i in range(n)]
    mf = [tp[i] * v[i] for i in range(n)]
    pos = [mf[i] if i and tp[i] > tp[i - 1] else 0.0 for i in range(n)]
    neg = [mf[i] if i and tp[i] < tp[i - 1] else 0.0 for i in range(n)]
    sp, sn = rolling(pos, 14, sum), rolling(neg, 14, sum)
    x["mfi"] = [100 - 100 / (1 + a / b) if ok(a, b) and b > 0 else (100.0 if ok(a, b) else math.nan) for a, b in zip(sp, sn)]
    hh14, ll14 = rolling(h, 14, max), rolling(l, 14, min)
    x["wr"] = [-100 * (hh14[i] - c[i]) / (hh14[i] - ll14[i]) if ok(hh14[i], ll14[i]) and hh14[i] > ll14[i] else math.nan for i in range(n)]
    tps = sma(tp, 20)
    md = [sum(abs(tp[j] - tps[i]) for j in range(i - 19, i + 1)) / 20 if ok(tps[i]) else math.nan for i in range(n)]
    x["cci"] = [(tp[i] - tps[i]) / (0.015 * md[i]) if ok(tps[i], md[i]) and md[i] > 0 else math.nan for i in range(n)]
    x["roc"] = [(c[i] / c[i - 12] - 1) * 100 if i >= 12 and c[i - 12] > 0 else math.nan for i in range(n)]
    x["adx"], x["pdi"], x["ndi"], x["watr"] = dmi(h, l, c)
    sd = rolling(c, 20, lambda w: (sum((a - sum(w) / 20) ** 2 for a in w) / 20) ** 0.5)
    x["bbu"] = [m + 2 * q if ok(m, q) else math.nan for m, q in zip(x["sma20"], sd)]
    x["bbl"] = [m - 2 * q if ok(m, q) else math.nan for m, q in zip(x["sma20"], sd)]
    x["bbw"] = [(u - lo) / m if ok(u, lo, m) and m else math.nan for u, lo, m in zip(x["bbu"], x["bbl"], x["sma20"])]
    x["stk"], x["std"] = stochastic(h, l, c)
    x["psar"], x["psar_up"] = psar(h, l)
    x["st"], x["st_up"] = supertrend(h, l, c)
    mid = lambda a, b: [(p + q) / 2 if ok(p, q) else math.nan for p, q in zip(a, b)]
    x["tenkan"] = mid(rolling(h, 9, max), rolling(l, 9, min))
    x["kijun"] = mid(rolling(h, 26, max), rolling(l, 26, min))
    span_a_raw, span_b_raw = mid(x["tenkan"], x["kijun"]), mid(rolling(h, 52, max), rolling(l, 52, min))
    x["span_a"] = [span_a_raw[i - 26] if i >= 26 else math.nan for i in range(n)]  # cloud plotted 26 ahead
    x["span_b"] = [span_b_raw[i - 26] if i >= 26 else math.nan for i in range(n)]
    x["rng"] = [h[i] - l[i] for i in range(n)]
    x["avgv20"] = sma(v, 20)
    # calendar week / month extremes
    wk = [dt.date.fromisoformat(z).isocalendar()[:2] for z in d]
    mo = [z[:7] for z in d]
    x["wk"], x["mo"] = wk, mo
    # benchmark-relative
    bc = [bench_close.get(z, math.nan) for z in d]
    x["bench"] = bc
    for p in (21, 55):
        x[f"rs{p}"] = [(c[i] / c[i - p]) / (bc[i] / bc[i - p]) - 1 if i >= p and ok(bc[i], bc[i - p]) and bc[i - p] > 0 and c[i - p] > 0
                       else math.nan for i in range(n)]
    # weekly bars
    keys, W = weekly(d, o, h, l, c, v)
    x["W"] = W
    for p in (5, 10, 20, 50):
        W[f"sma{p}"], W[f"ema{p}"] = sma(W["c"], p), ema(W["c"], p)
    W["rsi"] = rsi(W["c"])
    W["psar"], W["psar_up"] = psar(W["h"], W["l"])
    W["st"], W["st_up"] = supertrend(W["h"], W["l"], W["c"])
    W["rs21"] = []
    wbench = []
    for k in keys:
        # last benchmark close in the week
        idx = max(i for i in range(n) if wk[i] == k)
        wbench.append(bc[idx])
    W["rs21"] = [(W["c"][j] / W["c"][j - 21]) / (wbench[j] / wbench[j - 21]) - 1
                 if j >= 21 and ok(wbench[j], wbench[j - 21]) and wbench[j - 21] > 0 and W["c"][j - 21] > 0 else math.nan
                 for j in range(len(keys))]
    # beta vs NIFTY over up to 250 sessions
    rets = [(c[i] / c[i - 1] - 1, bc[i] / bc[i - 1] - 1) for i in range(max(1, n - 250), n)
            if c[i - 1] > 0 and ok(bc[i], bc[i - 1]) and bc[i - 1] > 0]
    if len(rets) >= 120:
        ms, mb = sum(a for a, _ in rets) / len(rets), sum(b for _, b in rets) / len(rets)
        cov = sum((a - ms) * (b - mb) for a, b in rets) / len(rets)
        var = sum((b - mb) ** 2 for _, b in rets) / len(rets)
        x["beta"] = cov / var if var > 0 else math.nan
    else:
        x["beta"] = math.nan
    return x


# ----------------------------------------------------------------------------- helpers
def cross_up(a, b, i) -> bool:
    bi, bp = (b[i], b[i - 1]) if isinstance(b, list) else (b, b)
    return i >= 1 and ok(a[i], a[i - 1], bi, bp) and a[i - 1] <= bp and a[i] > bi


def cross_dn(a, b, i) -> bool:
    bi, bp = (b[i], b[i - 1]) if isinstance(b, list) else (b, b)
    return i >= 1 and ok(a[i], a[i - 1], bi, bp) and a[i - 1] >= bp and a[i] < bi


def rising(a, i, k=3) -> bool:
    return i >= k and all(ok(a[j], a[j - 1]) and a[j] > a[j - 1] for j in range(i - k + 1, i + 1))


def falling(a, i, k=3) -> bool:
    return i >= k and all(ok(a[j], a[j - 1]) and a[j] < a[j - 1] for j in range(i - k + 1, i + 1))


def near(a, b, pct=1.0) -> bool:
    return ok(a, b) and b > 0 and abs(a / b - 1) * 100 <= pct


def prior_extreme(x, i, key, fn, period_key):
    """High/low of the previous calendar week/month (period_key 'wk'/'mo')."""
    cur = x[period_key][i]
    prev = None
    j = i
    while j >= 0 and x[period_key][j] == cur:
        j -= 1
    if j < 0:
        return math.nan
    prev = x[period_key][j]
    vals = []
    while j >= 0 and x[period_key][j] == prev:
        vals.append(x[key][j])
        j -= 1
    return fn(vals)


def prior_close(x, i, period_key):
    cur = x[period_key][i]
    j = i
    while j >= 0 and x[period_key][j] == cur:
        j -= 1
    return x["c"][j] if j >= 0 else math.nan


def current_extreme_before(x, i, key, fn, period_key):
    """High/low of the current week/month before today (needs at least one earlier bar)."""
    cur, vals, j = x[period_key][i], [], i - 1
    while j >= 0 and x[period_key][j] == cur:
        vals.append(x[key][j])
        j -= 1
    return fn(vals) if vals else math.nan


def ph(x, i, n):  # prior n-session high (excluding today)
    return max(x["h"][i - n:i]) if i >= n else math.nan


def pl(x, i, n):
    return min(x["l"][i - n:i]) if i >= n else math.nan


def body(x, i):
    return abs(x["c"][i] - x["o"][i])


def green(x, i):
    return x["c"][i] > x["o"][i]


def red(x, i):
    return x["c"][i] < x["o"][i]


def upper(x, i):
    return x["h"][i] - max(x["o"][i], x["c"][i])


def lower(x, i):
    return min(x["o"][i], x["c"][i]) - x["l"][i]


def downtrend(x, i):
    return i >= 5 and x["c"][i - 1] < x["c"][i - 5]


def uptrend(x, i):
    return i >= 5 and x["c"][i - 1] > x["c"][i - 5]


def doji(x, i):
    return x["rng"][i] > 0 and body(x, i) <= 0.1 * x["rng"][i]


def ret(x, i, n):
    return (x["c"][i] / x["c"][i - n] - 1) * 100 if i >= n and x["c"][i - n] > 0 else math.nan


# ----------------------------------------------------------------------------- volume (StockEdge 1000s)
@scan(1004, "higher-trade-quantity", "Higher trade quantity", "Volume", BULL, "Volume ≥ 2× 20-session average on an up day")
def _(x, i): return ok(x["avgv20"][i]) and x["v"][i] >= 2 * x["avgv20"][i] and x["c"][i] > x["c"][i - 1]


def _period_volume(x, i, key, lookback):
    cur = x[key][i]
    totals: dict = {}
    order = []
    for j in range(i, -1, -1):
        k = x[key][j]
        if k not in totals:
            if len(order) > lookback:
                break
            totals[k] = 0.0
            order.append(k)
        totals[k] += x["v"][j]
    prev = [totals[k] for k in order[1:lookback + 1]]
    return totals[cur], (sum(prev) / len(prev) if len(prev) == lookback else math.nan)


@scan(1014, "higher-trade-quantity-this-week", "Higher trade quantity this week", "Volume", BULL, "Week-to-date volume ≥ 2× the 4-week average, week up")
def _(x, i):
    now, avg = _period_volume(x, i, "wk", 4)
    return ok(avg) and now >= 2 * avg and x["c"][i] > prior_close(x, i, "wk")


@scan(1024, "higher-trade-quantity-this-month", "Higher trade quantity this month", "Volume", BULL, "Month-to-date volume ≥ 2× the 3-month average, month up")
def _(x, i):
    now, avg = _period_volume(x, i, "mo", 3)
    return ok(avg) and now >= 2 * avg and x["c"][i] > prior_close(x, i, "mo")


# ----------------------------------------------------------------------------- price action (2000s)
for _sid, _slug, _name, _key, _fn, _per, _bias in [
    (2001, "close-crossing-last-week-high", "Close crossing last week high", "h", max, "wk", BULL),
    (2002, "close-crossing-last-week-low", "Close crossing last week low", "l", min, "wk", BEAR),
    (2003, "close-crossing-last-month-high", "Close crossing last month high", "h", max, "mo", BULL),
    (2004, "close-crossing-last-month-low", "Close crossing last month low", "l", min, "mo", BEAR),
]:
    def _mk(key, fn, per, up):
        def t(x, i):
            lvl = prior_extreme(x, i, key, fn, per)
            return ok(lvl) and ((x["c"][i] > lvl and x["c"][i - 1] <= lvl) if up else (x["c"][i] < lvl and x["c"][i - 1] >= lvl))
        return t
    scan(_sid, _slug, _name, "Price action", _bias)(_mk(_key, _fn, _per, _bias == BULL))

for _sid, _slug, _name, _key, _fn, _per, _up in [
    (2062, "close-above-last-week-high", "Close above last week high", "h", max, "wk", True),
    (2061, "close-below-last-week-low", "Close below last week low", "l", min, "wk", False),
    (2064, "close-above-last-month-high", "Close above last month high", "h", max, "mo", True),
    (2063, "close-below-last-month-low", "Close below last month low", "l", min, "mo", False),
]:
    def _mk2(key, fn, per, up):
        def t(x, i):
            lvl = prior_extreme(x, i, key, fn, per)
            return ok(lvl) and (x["c"][i] > lvl if up else x["c"][i] < lvl)
        return t
    scan(_sid, _slug, _name, "Price action", BULL if _up else BEAR)(_mk2(_key, _fn, _per, _up))

for _sid, _slug, _name, _key, _fn, _per, _up in [
    (2055, "close-crossing-current-week-high", "Close crossing current week high", "h", max, "wk", True),
    (2056, "close-crossing-current-week-low", "Close crossing current week low", "l", min, "wk", False),
    (2053, "close-crossing-current-month-high", "Close crossing current month high", "h", max, "mo", True),
    (2054, "close-crossing-current-month-low", "Close crossing current month low", "l", min, "mo", False),
]:
    def _mk3(key, fn, per, up):
        def t(x, i):
            lvl = current_extreme_before(x, i, key, fn, per)
            return ok(lvl) and ((x["c"][i] > lvl >= x["c"][i - 1]) if up else (x["c"][i] < lvl <= x["c"][i - 1]))
        return t
    scan(_sid, _slug, _name, "Price action", BULL if _up else BEAR)(_mk3(_key, _fn, _per, _up))


@scan(2005, "close-crossing-last-month-close-from-below", "Close crossing last month close from below", "Price action", BULL)
def _(x, i): lvl = prior_close(x, i, "mo"); return ok(lvl) and x["c"][i - 1] <= lvl < x["c"][i]


@scan(2006, "close-crossing-last-month-close-from-above", "Close crossing last month close from above", "Price action", BEAR)
def _(x, i): lvl = prior_close(x, i, "mo"); return ok(lvl) and x["c"][i - 1] >= lvl > x["c"][i]


@scan(2007, "close-crossing-52-week-high", "Close crossing 52 week high", "Price action", BULL, "Close above the prior 250-session high")
def _(x, i): return i >= 251 and x["c"][i] > ph(x, i, 250) and x["c"][i - 1] <= ph(x, i - 1, 250)


@scan(2008, "close-crossing-52-week-low", "Close crossing 52 week low", "Price action", BEAR, "Close below the prior 250-session low")
def _(x, i): return i >= 251 and x["c"][i] < pl(x, i, 250) and x["c"][i - 1] >= pl(x, i - 1, 250)


def _zone_high(x, i): return i >= 250 and x["c"][i] >= 0.95 * max(x["h"][i - 249:i + 1])
def _zone_low(x, i): return i >= 250 and x["c"][i] <= 1.05 * min(x["l"][i - 249:i + 1])


@scan(2019, "close-within-52-week-high-zone", "Close within 52 week high zone", "Price action", BULL, "Within 5% of the 52-week high")
def _(x, i): return _zone_high(x, i)


@scan(2020, "close-entering-52-week-high-zone", "Close entering 52 week high zone", "Price action", BULL, "Entered the 5% band below the 52-week high today")
def _(x, i): return _zone_high(x, i) and not _zone_high(x, i - 1)


@scan(2021, "close-within-52-week-low-zone", "Close within 52 week low zone", "Price action", BEAR, "Within 5% of the 52-week low")
def _(x, i): return _zone_low(x, i)


@scan(2022, "close-entering-52-week-low-zone", "Close entering 52 week low zone", "Price action", BEAR, "Entered the 5% band above the 52-week low today")
def _(x, i): return _zone_low(x, i) and not _zone_low(x, i - 1)


for _sid, _slug, _name, _cond, _bias in [
    (2009, "opening-at-high", "Opening at high", lambda x, i: x["o"][i] == x["h"][i], BEAR),
    (2010, "opening-at-low", "Opening at low", lambda x, i: x["o"][i] == x["l"][i], BULL),
]:
    scan(_sid, _slug, _name, "Price action", _bias)(_cond)
    def _mkn(cond, k):
        return lambda x, i: i >= k - 1 and all(cond(x, j) for j in range(i - k + 1, i + 1))
    scan(_sid + 2, _slug + "-for-3-days", _name + " for 3 days", "Price action", _bias)(_mkn(_cond, 3))
    scan({2009: 2081, 2010: 2082}[_sid], _slug + "-for-2-days", _name + " for 2 days", "Price action", _bias)(_mkn(_cond, 2))


def _consec(cond, k):
    return lambda x, i: i >= k and all(cond(x, j) for j in range(i - k + 1, i + 1))


_cap = lambda x, j: x["c"][j] > x["h"][j - 1]
_cbl = lambda x, j: x["c"][j] < x["l"][j - 1]
_hh = lambda x, j: x["h"][j] > x["h"][j - 1]
_ll = lambda x, j: x["l"][j] < x["l"][j - 1]
for _sid, _slug, _name, _cond, _k, _bias in [
    (2013, "closing-above-previous-high", "Closing above previous high", _cap, 1, BULL),
    (2014, "closing-below-previous-low", "Closing below previous low", _cbl, 1, BEAR),
    (2083, "closing-above-previous-high-for-2-days", "Closing above previous high for 2 days", _cap, 2, BULL),
    (2084, "closing-below-previous-low-for-2-days", "Closing below previous low for 2 days", _cbl, 2, BEAR),
    (2015, "closing-above-previous-high-for-3-days", "Closing above previous high for 3 days", _cap, 3, BULL),
    (2016, "closing-below-previous-low-for-3-days", "Closing below previous low for 3 days", _cbl, 3, BEAR),
    (2085, "making-higher-highs-for-2-days", "Making higher highs for 2 days", _hh, 2, BULL),
    (2086, "making-lower-lows-for-2-days", "Making lower lows for 2 days", _ll, 2, BEAR),
    (2017, "making-higher-highs-for-3-days", "Making higher highs for 3 days", _hh, 3, BULL),
    (2018, "making-lower-lows-for-3-days", "Making lower lows for 3 days", _ll, 3, BEAR),
]:
    scan(_sid, _slug, _name, "Price action", _bias)(_consec(_cond, _k))


# rise / fall bands
def _band(fn_ref, lo, hi, up):
    def t(x, i):
        if i < 250:
            return False
        ref = fn_ref(x, i)
        chg = (x["c"][i] / ref - 1) * 100 if ref > 0 else math.nan
        val = chg if up else -chg
        return ok(val) and lo <= val < hi
    return t


_low52 = lambda x, i: min(x["l"][i - 249:i + 1])
_high52 = lambda x, i: max(x["h"][i - 249:i + 1])
for _sid, _slug, _name, _lo, _hi in [
    (2231, "moderate-rise-from-52-week-low", "Moderate rise from 52 week low", 10, 25),
    (2232, "moderately-high-rise-from-52-week-low", "Moderately high rise from 52 week low", 25, 50),
    (2233, "high-rise-from-52-week-low", "High rise from 52 week low", 50, 100),
    (2234, "very-high-rise-from-52-week-low", "Very high rise from 52 week low", 100, 1e9),
]:
    scan(_sid, _slug, _name, "Price action", BULL, f"{_lo}–{_hi if _hi < 1e9 else '∞'}% above the 52-week low")(_band(_low52, _lo, _hi, True))
for _sid, _slug, _name, _lo, _hi in [
    (2236, "moderate-fall-from-52-week-high", "Moderate fall from 52 week high", 10, 25),
    (2237, "moderately-high-fall-from-52-week-high", "Moderately high fall from 52 week high", 25, 50),
    (2238, "high-fall-from-52-week-high", "High fall from 52 week high", 50, 75),
    (2239, "very-high-fall-from-52-week-high", "Very high fall from 52 week high", 75, 101),
]:
    scan(_sid, _slug, _name, "Price action", BEAR, f"{_lo}–{_hi}% below the 52-week high")(_band(_high52, _lo, _hi, False))

for _sid, _n, _label, _thr in [(2101, 21, "1 month", 20), (2103, 63, "3 months", 30), (2105, 126, "6 months", 50), (2107, 250, "1 year", 75)]:
    scan(_sid, f"high-increase-in-{_label.replace(' ', '-')}", f"High increase in {_label}", "Price action", BULL, f"Up ≥ {_thr}%")(
        (lambda n, t: lambda x, i: ok(ret(x, i, n)) and ret(x, i, n) >= t)(_n, _thr))
    scan(_sid + 1, f"high-decrease-in-{_label.replace(' ', '-')}", f"High decrease in {_label}", "Price action", BEAR, f"Down ≥ {_thr}%")(
        (lambda n, t: lambda x, i: ok(ret(x, i, n)) and ret(x, i, n) <= -t)(_n, _thr))


# relative strength vs NIFTY 50
def _bench_ret(x, i, n):
    b = x["bench"]
    return (b[i] / b[i - n] - 1) * 100 if i >= n and ok(b[i], b[i - n]) and b[i - n] > 0 else math.nan


@scan(2023, "stock-outperforming-benchmark-index-in-both-1-week-and-3-month", "Outperforming NIFTY 50 in both 1 week and 3 months", "Relative strength", BULL)
def _(x, i): return ok(ret(x, i, 5), ret(x, i, 63), _bench_ret(x, i, 5), _bench_ret(x, i, 63)) and ret(x, i, 5) > _bench_ret(x, i, 5) and ret(x, i, 63) > _bench_ret(x, i, 63)


@scan(2024, "stock-underperforming-benchmark-index-in-both-1-week-and-3-month", "Underperforming NIFTY 50 in both 1 week and 3 months", "Relative strength", BEAR)
def _(x, i): return ok(ret(x, i, 5), ret(x, i, 63), _bench_ret(x, i, 5), _bench_ret(x, i, 63)) and ret(x, i, 5) < _bench_ret(x, i, 5) and ret(x, i, 63) < _bench_ret(x, i, 63)


for _p, (_strong_up, _strong_dn, _inc, _dec, _above, _below) in {
    55: (2027, 2028, 2029, 2030, 2057, 2058), 21: (2301, 2302, 2303, 2304, 2305, 2306)}.items():
    key = f"rs{_p}"
    lab = f"{_p} days"
    scan(_strong_up, f"strongly-outperforming-benchmark-index-{_p}-days", f"Strongly outperforming NIFTY 50 ({lab})", "Relative strength", BULL, "Relative strength > +10%")(
        (lambda k: lambda x, i: ok(x[k][i]) and x[k][i] > 0.10)(key))
    scan(_strong_dn, f"strongly-underperforming-benchmark-index-{_p}-days", f"Strongly underperforming NIFTY 50 ({lab})", "Relative strength", BEAR, "Relative strength < −10%")(
        (lambda k: lambda x, i: ok(x[k][i]) and x[k][i] < -0.10)(key))
    scan(_inc, f"increasing-relative-strength-benchmark-index-{_p}-days", f"Increasing relative strength vs NIFTY 50 ({lab})", "Relative strength", BULL, "Rising for 3 sessions")(
        (lambda k: lambda x, i: rising(x[k], i, 3))(key))
    scan(_dec, f"decreasing-relative-strength-benchmark-index-{_p}-days", f"Decreasing relative strength vs NIFTY 50 ({lab})", "Relative strength", BEAR, "Falling for 3 sessions")(
        (lambda k: lambda x, i: falling(x[k], i, 3))(key))
    scan(_above, f"relative-strength-going-above-zero-benchmark-index-{_p}-days", f"Relative strength going above zero ({lab})", "Relative strength", BULL)(
        (lambda k: lambda x, i: cross_up(x[k], 0.0, i))(key))
    scan(_below, f"relative-strength-going-below-zero-benchmark-index-{_p}-days", f"Relative strength going below zero ({lab})", "Relative strength", BEAR)(
        (lambda k: lambda x, i: cross_dn(x[k], 0.0, i))(key))

# ----------------------------------------------------------------------------- VWAP (daily)
_abv = lambda x, j: ok(x["vw"][j]) and x["c"][j] > x["vw"][j]
_blw = lambda x, j: ok(x["vw"][j]) and x["c"][j] < x["vw"][j]
scan(2221, "close-crossing-above-vwap", "Close crossing above VWAP", "VWAP", BULL, "Session VWAP = traded value ÷ volume")(lambda x, i: cross_up(x["c"], x["vw"], i))
scan(2222, "close-crossing-below-vwap", "Close crossing below VWAP", "VWAP", BEAR)(lambda x, i: cross_dn(x["c"], x["vw"], i))
scan(2223, "closing-above-vwap-for-2-days", "Closing above VWAP for 2 days", "VWAP", BULL)(lambda x, i: _abv(x, i) and _abv(x, i - 1))
scan(2224, "closing-below-vwap-for-2-days", "Closing below VWAP for 2 days", "VWAP", BEAR)(lambda x, i: _blw(x, i) and _blw(x, i - 1))
scan(2225, "closing-above-vwap-for-3-days", "Closing above VWAP for 3 days", "VWAP", BULL)(lambda x, i: all(_abv(x, j) for j in (i, i - 1, i - 2)))
scan(2226, "closing-below-vwap-for-3-days", "Closing below VWAP for 3 days", "VWAP", BEAR)(lambda x, i: all(_blw(x, j) for j in (i, i - 1, i - 2)))

# ----------------------------------------------------------------------------- moving averages (3000s)
for _kind, _base_near, _cross_ids, _near_extra in (
    ("sma", {50: 3001, 100: 3002, 200: 3003, 20: 3024},
     {50: (3004, 3005), 100: (3006, 3007), 200: (3008, 3009), 20: (3025, 3026)}, {}),
    ("ema", {50: 3050, 100: 3051, 200: 3052, 20: 3075},
     {20: (3073, 3074), 50: (3053, 3054), 100: (3055, 3056), 200: (3057, 3058)}, {}),
):
    K = _kind.upper()
    for p, sid in _base_near.items():
        scan(sid, f"closing-near-{p}-{_kind}", f"Closing near {p} {K}", "Moving averages", NEUTRAL, "Within 1%")(
            (lambda key: lambda x, i: near(x["c"][i], x[key][i], 1.0))(f"{_kind}{p}"))
    for p, (up_id, dn_id) in _cross_ids.items():
        key = f"{_kind}{p}"
        scan(up_id, f"close-crossing-{p}-{_kind}-from-below", f"Close crossing {p} {K} from below", "Moving averages", BULL)(
            (lambda k: lambda x, i: cross_up(x["c"], x[k], i))(key))
        scan(dn_id, f"close-crossing-{p}-{_kind}-from-above", f"Close crossing {p} {K} from above", "Moving averages", BEAR)(
            (lambda k: lambda x, i: cross_dn(x["c"], x[k], i))(key))

for _kind, pairs in (
    ("sma", {(50, 100): 3010, (100, 200): 3012, (50, 200): 3014, (20, 50): 3016, (5, 20): 3018, (20, 100): 3020, (20, 200): 3022}),
    ("ema", {(50, 100): 3059, (100, 200): 3061, (50, 200): 3063, (20, 50): 3065, (5, 20): 3067, (20, 100): 3069, (20, 200): 3071}),
):
    K = _kind.upper()
    for (a, b), sid in pairs.items():
        ka, kb = f"{_kind}{a}", f"{_kind}{b}"
        scan(sid, f"{a}-{_kind}-crossing-{b}-{_kind}-from-below", f"{a} {K} crossing {b} {K} from below", "Moving averages", BULL)(
            (lambda p, q: lambda x, i: cross_up(x[p], x[q], i))(ka, kb))
        scan(sid + 1, f"{a}-{_kind}-crossing-{b}-{_kind}-from-above", f"{a} {K} crossing {b} {K} from above", "Moving averages", BEAR)(
            (lambda p, q: lambda x, i: cross_dn(x[p], x[q], i))(ka, kb))

# ----------------------------------------------------------------------------- oscillators
def _osc(prefix, key, ids, upper, lower, cat, note):
    bull, tup, xup, bear, tdn, xdn = ids
    scan(bull, f"{prefix}-bullish", f"{prefix.upper().replace('-', ' ')} bullish", cat, BULL, f"Crossed above 50 · {note}")(lambda x, i: cross_up(x[key], 50.0, i))
    scan(tup, f"{prefix}-trending-up", f"{prefix.upper().replace('-', ' ')} trending up", cat, BULL, "Rising 3 sessions")(lambda x, i: rising(x[key], i, 3))
    scan(bear, f"{prefix}-bearish", f"{prefix.upper().replace('-', ' ')} bearish", cat, BEAR, "Crossed below 50")(lambda x, i: cross_dn(x[key], 50.0, i))
    scan(tdn, f"{prefix}-trending-down", f"{prefix.upper().replace('-', ' ')} trending down", cat, BEAR, "Falling 3 sessions")(lambda x, i: falling(x[key], i, 3))
    if xup:
        scan(xup, f"{prefix}-crossed-above-{upper}", f"{prefix.upper()} crossed above {upper}", cat, BULL)(lambda x, i: cross_up(x[key], float(upper), i))
    if xdn:
        scan(xdn, f"{prefix}-crossed-below-{lower}", f"{prefix.upper()} crossed below {lower}", cat, BEAR)(lambda x, i: cross_dn(x[key], float(lower), i))


_osc("rsi", "rsi", (3107, 3108, 3109, 3110, 3111, 3112), 70, 30, "Oscillators", "RSI(14)")
scan(3221, "rsi-overbought-zone", "RSI overbought zone", "Oscillators", NEUTRAL, "RSI ≥ 70")(lambda x, i: ok(x["rsi"][i]) and x["rsi"][i] >= 70)
scan(3222, "rsi-oversold-zone", "RSI oversold zone", "Oscillators", NEUTRAL, "RSI ≤ 30")(lambda x, i: ok(x["rsi"][i]) and x["rsi"][i] <= 30)

scan(3113, "mfi-bullish", "MFI bullish", "Oscillators", BULL, "MFI(14) crossed above 50")(lambda x, i: cross_up(x["mfi"], 50.0, i))
scan(3114, "mfi-trending-up", "MFI trending up", "Oscillators", BULL)(lambda x, i: rising(x["mfi"], i, 3))
scan(3115, "mfi-crossed-80-from-below", "MFI crossed 80 from below", "Oscillators", BULL)(lambda x, i: cross_up(x["mfi"], 80.0, i))
scan(3116, "mfi-bearish", "MFI bearish", "Oscillators", BEAR)(lambda x, i: cross_dn(x["mfi"], 50.0, i))
scan(3117, "mfi-trending-down", "MFI trending down", "Oscillators", BEAR)(lambda x, i: falling(x["mfi"], i, 3))
scan(3118, "mfi-crossed-20-from-above", "MFI crossed 20 from above", "Oscillators", BEAR)(lambda x, i: cross_dn(x["mfi"], 20.0, i))
scan(3231, "mfi-above-80", "MFI above 80", "Oscillators", NEUTRAL)(lambda x, i: ok(x["mfi"][i]) and x["mfi"][i] > 80)
scan(3232, "mfi-below-20", "MFI below 20", "Oscillators", NEUTRAL)(lambda x, i: ok(x["mfi"][i]) and x["mfi"][i] < 20)

scan(3101, "cci-bullish", "CCI bullish", "Oscillators", BULL, "CCI(20) crossed above 0")(lambda x, i: cross_up(x["cci"], 0.0, i))
scan(3102, "cci-trending-up", "CCI trending up", "Oscillators", BULL)(lambda x, i: rising(x["cci"], i, 3))
scan(3103, "cci-crossed-above-100", "CCI crossed above 100", "Oscillators", BULL)(lambda x, i: cross_up(x["cci"], 100.0, i))
scan(3104, "cci-bearish", "CCI bearish", "Oscillators", BEAR)(lambda x, i: cross_dn(x["cci"], 0.0, i))
scan(3105, "cci-trending-down", "CCI trending down", "Oscillators", BEAR)(lambda x, i: falling(x["cci"], i, 3))
scan(3106, "cci-crossed-below-100", "CCI crossed below −100", "Oscillators", BEAR)(lambda x, i: cross_dn(x["cci"], -100.0, i))
scan(3211, "cci-overbought-zone", "CCI overbought zone", "Oscillators", NEUTRAL, "CCI > 100")(lambda x, i: ok(x["cci"][i]) and x["cci"][i] > 100)
scan(3212, "cci-oversold-zone", "CCI oversold zone", "Oscillators", NEUTRAL, "CCI < −100")(lambda x, i: ok(x["cci"][i]) and x["cci"][i] < -100)

scan(3119, "william-r-bullish", "Williams %R bullish", "Oscillators", BULL, "%R(14) crossed above −50")(lambda x, i: cross_up(x["wr"], -50.0, i))
scan(3120, "william-r-trending-up", "Williams %R trending up", "Oscillators", BULL)(lambda x, i: rising(x["wr"], i, 3))
scan(3121, "william-r-crossed-20-from-below", "Williams %R crossed −20 from below", "Oscillators", BULL)(lambda x, i: cross_up(x["wr"], -20.0, i))
scan(3122, "william-r-bearish", "Williams %R bearish", "Oscillators", BEAR)(lambda x, i: cross_dn(x["wr"], -50.0, i))
scan(3123, "william-r-trending-down", "Williams %R trending down", "Oscillators", BEAR)(lambda x, i: falling(x["wr"], i, 3))
scan(3124, "william-r-crossed-80-from-above", "Williams %R crossed −80 from above", "Oscillators", BEAR)(lambda x, i: cross_dn(x["wr"], -80.0, i))
scan(3131, "william-%r-in-overbought-zone", "Williams %R in overbought zone", "Oscillators", NEUTRAL, "%R > −20")(lambda x, i: ok(x["wr"][i]) and x["wr"][i] > -20)
scan(3132, "william-%r-in-oversold-zone", "Williams %R in oversold zone", "Oscillators", NEUTRAL, "%R < −80")(lambda x, i: ok(x["wr"][i]) and x["wr"][i] < -80)

scan(3125, "roc-trending-up", "ROC trending up", "Oscillators", BULL, "ROC(12) rising 3 sessions")(lambda x, i: rising(x["roc"], i, 3))
scan(3126, "roc-trending-down", "ROC trending down", "Oscillators", BEAR)(lambda x, i: falling(x["roc"], i, 3))

scan(3127, "macd-crossing-signal-line-from-below", "MACD crossing signal line from below", "Oscillators", BULL, "MACD 12/26/9")(lambda x, i: cross_up(x["macd"], x["macds"], i))
scan(3128, "macd-crossing-signal-line-from-above", "MACD crossing signal line from above", "Oscillators", BEAR)(lambda x, i: cross_dn(x["macd"], x["macds"], i))
scan(3129, "macd-moving-above-zero", "MACD moving above zero", "Oscillators", BULL)(lambda x, i: cross_up(x["macd"], 0.0, i))
scan(3130, "macd-moving-below-zero", "MACD moving below zero", "Oscillators", BEAR)(lambda x, i: cross_dn(x["macd"], 0.0, i))

# stochastic (14,3,3)
scan(3181, "stochastic-reversing-from-oversold-zone-from-below", "Stochastic reversing from oversold zone", "Oscillators", BULL, "%K crossed above 20")(lambda x, i: cross_up(x["stk"], 20.0, i))
scan(3182, "stochastic-trending-up", "Stochastic trending up", "Oscillators", BULL)(lambda x, i: rising(x["stk"], i, 3))
scan(3183, "stochastic-entering-overbought-zone-from-below", "Stochastic entering overbought zone", "Oscillators", NEUTRAL, "%K crossed above 80")(lambda x, i: cross_up(x["stk"], 80.0, i))
scan(3184, "stochastic-reversing-from-overbought-zone", "Stochastic reversing from overbought zone", "Oscillators", BEAR, "%K crossed below 80")(lambda x, i: cross_dn(x["stk"], 80.0, i))
scan(3185, "stochastic-trending-down", "Stochastic trending down", "Oscillators", BEAR)(lambda x, i: falling(x["stk"], i, 3))
scan(3186, "stochastic-entering-oversold-zone-from-above", "Stochastic entering oversold zone", "Oscillators", NEUTRAL, "%K crossed below 20")(lambda x, i: cross_dn(x["stk"], 20.0, i))
scan(3187, "stochastic-k-crossing-d-from-below", "Stochastic %K crossing %D from below", "Oscillators", BULL)(lambda x, i: cross_up(x["stk"], x["std"], i))
scan(3188, "stochastic-k-crossing-d-from-above", "Stochastic %K crossing %D from above", "Oscillators", BEAR)(lambda x, i: cross_dn(x["stk"], x["std"], i))
scan(3189, "stochastic-in-overbought-zone", "Stochastic in overbought zone", "Oscillators", NEUTRAL, "%K > 80")(lambda x, i: ok(x["stk"][i]) and x["stk"][i] > 80)
scan(3190, "stochastic-in-oversold-zone", "Stochastic in oversold zone", "Oscillators", NEUTRAL, "%K < 20")(lambda x, i: ok(x["stk"][i]) and x["stk"][i] < 20)

# ----------------------------------------------------------------------------- trend indicators
scan(3151, "adx-crossing-25-from-below", "ADX crossing 25 from below", "Trend", NEUTRAL, "ADX(14): trend strengthening")(lambda x, i: cross_up(x["adx"], 25.0, i))
scan(3152, "adx-crossing-40-from-above", "ADX crossing 40 from above", "Trend", NEUTRAL)(lambda x, i: cross_dn(x["adx"], 40.0, i))
scan(3153, "adx-crossing-10-from-below", "ADX crossing 10 from below", "Trend", NEUTRAL)(lambda x, i: cross_up(x["adx"], 10.0, i))
scan(3154, "adx-crossing-25-from-above", "ADX crossing 25 from above", "Trend", NEUTRAL)(lambda x, i: cross_dn(x["adx"], 25.0, i))
scan(3155, "adx-crossing-40-from-below", "ADX crossing 40 from below", "Trend", NEUTRAL)(lambda x, i: cross_up(x["adx"], 40.0, i))
scan(3156, "adx-crossing-10-from-above", "ADX crossing 10 from above", "Trend", NEUTRAL)(lambda x, i: cross_dn(x["adx"], 10.0, i))
scan(3157, "+di-crossing-di-from-below", "+DI crossing −DI from below", "Trend", BULL)(lambda x, i: cross_up(x["pdi"], x["ndi"], i))
scan(3158, "+di-crossing-di-from-above", "+DI crossing −DI from above", "Trend", BEAR)(lambda x, i: cross_dn(x["pdi"], x["ndi"], i))
scan(3159, "di-crossing-25-from-below", "+DI crossing 25 from below", "Trend", BULL)(lambda x, i: cross_up(x["pdi"], 25.0, i))
scan(3160, "-di-crossing-25-from-below", "−DI crossing 25 from below", "Trend", BEAR)(lambda x, i: cross_up(x["ndi"], 25.0, i))
for _k, _up, _dn in ((3, 3161, 3164), (5, 3162, 3165), (7, 3163, 3166)):
    scan(_up, f"atr-increasing-for-{_k}-days", f"ATR increasing for {_k} days", "Volatility", NEUTRAL, "Wilder ATR(14)")((lambda k: lambda x, i: rising(x["watr"], i, k))(_k))
    scan(_dn, f"atr-decreasing-for-{_k}-days", f"ATR decreasing for {_k} days", "Volatility", NEUTRAL)((lambda k: lambda x, i: falling(x["watr"], i, k))(_k))

scan(3171, "close-crossing-upper-bollinger-band-from-below", "Close crossing upper Bollinger Band from below", "Volatility", BULL, "Bollinger 20, 2σ")(lambda x, i: cross_up(x["c"], x["bbu"], i))
scan(3172, "close-crossing-lower-bollinger-band-from-above", "Close crossing lower Bollinger Band from above", "Volatility", BEAR)(lambda x, i: cross_dn(x["c"], x["bbl"], i))
scan(3173, "close-crossing-upper-bollinger-band-from-above", "Close crossing upper Bollinger Band from above", "Volatility", BEAR)(lambda x, i: cross_dn(x["c"], x["bbu"], i))
scan(3174, "close-crossing-lower-bollinger-band-from-below", "Close crossing lower Bollinger Band from below", "Volatility", BULL)(lambda x, i: cross_up(x["c"], x["bbl"], i))
scan(3177, "close-above-upper-bollinger-band", "Close above upper Bollinger Band", "Volatility", BULL)(lambda x, i: ok(x["bbu"][i]) and x["c"][i] > x["bbu"][i])
scan(3178, "close-below-lower-bollinger-band", "Close below lower Bollinger Band", "Volatility", BEAR)(lambda x, i: ok(x["bbl"][i]) and x["c"][i] < x["bbl"][i])


def _bbw_pct(x, i):
    w = [z for z in x["bbw"][max(0, i - 119):i + 1] if ok(z)]
    return (sum(1 for z in w if z <= x["bbw"][i]) / len(w)) if len(w) >= 60 and ok(x["bbw"][i]) else math.nan


scan(3175, "narrow-bollinger-band-width", "Narrow Bollinger Band width", "Volatility", NEUTRAL, "Width in the lowest 10% of 120 sessions (squeeze)")(lambda x, i: ok(_bbw_pct(x, i)) and _bbw_pct(x, i) <= 0.10)
scan(3176, "wide-bollinger-band-width", "Wide Bollinger Band width", "Volatility", NEUTRAL, "Width in the highest 10% of 120 sessions")(lambda x, i: ok(_bbw_pct(x, i)) and _bbw_pct(x, i) >= 0.90)

scan(3201, "psar-indicating-bullish-reversal", "PSAR indicating bullish reversal", "Trend", BULL, "Parabolic SAR 0.02/0.2 flipped below price")(lambda x, i: x["psar_up"][i] is True and x["psar_up"][i - 1] is False)
scan(3202, "psar-indicating-bearish-reversal", "PSAR indicating bearish reversal", "Trend", BEAR)(lambda x, i: x["psar_up"][i] is False and x["psar_up"][i - 1] is True)
scan(3247, "supertrend-signal-changed-to-buy", "Supertrend signal changed to buy", "Trend", BULL, "Supertrend 10, 3")(lambda x, i: x["st_up"][i] is True and x["st_up"][i - 1] is False)
scan(3248, "supertrend-signal-changed-to-sell", "Supertrend signal changed to sell", "Trend", BEAR)(lambda x, i: x["st_up"][i] is False and x["st_up"][i - 1] is True)
scan(3249, "price-nearing-supertrend-support", "Price nearing Supertrend support", "Trend", BULL, "Buy mode, close within 2% of Supertrend")(lambda x, i: x["st_up"][i] is True and near(x["c"][i], x["st"][i], 2.0))
scan(3250, "price-nearing-supertrend-resistance", "Price nearing Supertrend resistance", "Trend", BEAR, "Sell mode, close within 2% of Supertrend")(lambda x, i: x["st_up"][i] is False and near(x["c"][i], x["st"][i], 2.0))


def _nr(x, i, k):
    return i >= k - 1 and x["rng"][i] <= min(x["rng"][i - k + 1:i + 1])


scan(3241, "nr-4", "NR4", "Volatility", NEUTRAL, "Narrowest range of 4 sessions")(lambda x, i: _nr(x, i, 4))
scan(3242, "nr-7", "NR7", "Volatility", NEUTRAL, "Narrowest range of 7 sessions")(lambda x, i: _nr(x, i, 7))
scan(3243, "nr-4-breakout", "NR4 breakout", "Volatility", BULL, "Yesterday NR4, today closes above its high")(lambda x, i: _nr(x, i - 1, 4) and x["c"][i] > x["h"][i - 1])
scan(3244, "nr-4-breakdown", "NR4 breakdown", "Volatility", BEAR)(lambda x, i: _nr(x, i - 1, 4) and x["c"][i] < x["l"][i - 1])
scan(3245, "nr-7-breakout", "NR7 breakout", "Volatility", BULL)(lambda x, i: _nr(x, i - 1, 7) and x["c"][i] > x["h"][i - 1])
scan(3246, "nr-7-breakdown", "NR7 breakdown", "Volatility", BEAR)(lambda x, i: _nr(x, i - 1, 7) and x["c"][i] < x["l"][i - 1])

# Ichimoku (9, 26, 52)
for _line, (_ns, _nr_, _cu, _cd) in {"tenkan": (3271, 3272, 3273, 3274), "kijun": (3275, 3276, 3277, 3278)}.items():
    L = "Tenkan-sen" if _line == "tenkan" else "Kijun-sen"
    scan(_ns, f"price-near-{_line}-sen-support", f"Price near {L} support", "Ichimoku", BULL, "Close above and within 1%")(
        (lambda k: lambda x, i: ok(x[k][i]) and x["c"][i] >= x[k][i] and near(x["c"][i], x[k][i], 1.0))(_line))
    scan(_nr_, f"price-near-{_line}-sen-resistance", f"Price near {L} resistance", "Ichimoku", BEAR, "Close below and within 1%")(
        (lambda k: lambda x, i: ok(x[k][i]) and x["c"][i] < x[k][i] and near(x["c"][i], x[k][i], 1.0))(_line))
    scan(_cu, f"close-crossing-{_line}-sen-from-below", f"Close crossing {L} from below", "Ichimoku", BULL)((lambda k: lambda x, i: cross_up(x["c"], x[k], i))(_line))
    scan(_cd, f"close-crossing-{_line}-sen-from-above", f"Close crossing {L} from above", "Ichimoku", BEAR)((lambda k: lambda x, i: cross_dn(x["c"], x[k], i))(_line))
scan(3279, "tenkan-sen-crossing-kijun-sen-from-below", "Tenkan-sen crossing Kijun-sen from below", "Ichimoku", BULL)(lambda x, i: cross_up(x["tenkan"], x["kijun"], i))
scan(3280, "tenkan-sen-crossing-kijun-sen-from-above", "Tenkan-sen crossing Kijun-sen from above", "Ichimoku", BEAR)(lambda x, i: cross_dn(x["tenkan"], x["kijun"], i))
scan(3281, "senkou-span-a-crossing-senkou-span-b-from-below", "Senkou Span A crossing Span B from below", "Ichimoku", BULL)(lambda x, i: cross_up(x["span_a"], x["span_b"], i))
scan(3282, "senkou-span-a-crossing-senkou-span-b-from-above", "Senkou Span A crossing Span B from above", "Ichimoku", BEAR)(lambda x, i: cross_dn(x["span_a"], x["span_b"], i))
scan(3283, "chikou-span-crossing-price-from-below", "Chikou Span crossing price from below", "Ichimoku", BULL, "Close crossing the close 26 sessions ago")(
    lambda x, i: i >= 27 and x["c"][i - 1] <= x["c"][i - 27] and x["c"][i] > x["c"][i - 26])
scan(3284, "chikou-span-crossing-price-from-above", "Chikou Span crossing price from above", "Ichimoku", BEAR)(
    lambda x, i: i >= 27 and x["c"][i - 1] >= x["c"][i - 27] and x["c"][i] < x["c"][i - 26])

# beta vs NIFTY 50
scan(3251, "high-beta-benchmark-index", "High beta (NIFTY 50)", "Risk", NEUTRAL, "β > 1.2 over up to 250 sessions")(lambda x, i: ok(x["beta"]) and x["beta"] > 1.2)
scan(3252, "low-beta-benchmark-index", "Low beta (NIFTY 50)", "Risk", NEUTRAL, "0 ≤ β < 0.8")(lambda x, i: ok(x["beta"]) and 0 <= x["beta"] < 0.8)
scan(3253, "negative-beta-benchmark-index", "Negative beta (NIFTY 50)", "Risk", NEUTRAL)(lambda x, i: ok(x["beta"]) and x["beta"] < 0)

# ----------------------------------------------------------------------------- candlesticks (6000s)
scan(6001, "doji-and-near-doji", "Doji and near doji", "Candlesticks", NEUTRAL, "Body ≤ 10% of range")(lambda x, i: doji(x, i))
scan(6002, "white-marubozu", "White marubozu", "Candlesticks", BULL, "Green body ≥ 90% of range")(lambda x, i: green(x, i) and x["rng"][i] > 0 and body(x, i) >= 0.9 * x["rng"][i])
scan(6003, "black-marubozu", "Black marubozu", "Candlesticks", BEAR)(lambda x, i: red(x, i) and x["rng"][i] > 0 and body(x, i) >= 0.9 * x["rng"][i])


def _hammer_shape(x, i):
    b, r = body(x, i), x["rng"][i]
    return r > 0 and b > 0 and lower(x, i) >= 2 * b and upper(x, i) <= 0.1 * r


def _star_shape(x, i):
    b, r = body(x, i), x["rng"][i]
    return r > 0 and b > 0 and upper(x, i) >= 2 * b and lower(x, i) <= 0.1 * r


scan(6006, "hammer", "Hammer", "Candlesticks", BULL, "After a 5-session decline")(lambda x, i: _hammer_shape(x, i) and downtrend(x, i))
scan(6004, "hanging-man", "Hanging man", "Candlesticks", BEAR, "After a 5-session rise")(lambda x, i: _hammer_shape(x, i) and uptrend(x, i))
scan(6005, "shooting-star", "Shooting star", "Candlesticks", BEAR, "After a 5-session rise")(lambda x, i: _star_shape(x, i) and uptrend(x, i))
scan(6009, "inverted-hammer", "Inverted hammer", "Candlesticks", BULL, "After a 5-session decline")(lambda x, i: _star_shape(x, i) and downtrend(x, i))
scan(6008, "dragonfly-doji", "Dragonfly doji", "Candlesticks", BULL)(lambda x, i: doji(x, i) and upper(x, i) <= 0.1 * x["rng"][i] and lower(x, i) >= 0.6 * x["rng"][i])
scan(6011, "gravestone-doji", "Gravestone doji", "Candlesticks", BEAR)(lambda x, i: doji(x, i) and lower(x, i) <= 0.1 * x["rng"][i] and upper(x, i) >= 0.6 * x["rng"][i])
scan(6007, "bullish-engulfing", "Bullish engulfing", "Candlesticks", BULL)(
    lambda x, i: red(x, i - 1) and green(x, i) and x["o"][i] <= x["c"][i - 1] and x["c"][i] >= x["o"][i - 1])
scan(6026, "bearish-engulfing", "Bearish engulfing", "Candlesticks", BEAR)(
    lambda x, i: green(x, i - 1) and red(x, i) and x["o"][i] >= x["c"][i - 1] and x["c"][i] <= x["o"][i - 1])


def _inside_body(x, i):
    return max(x["o"][i], x["c"][i]) < max(x["o"][i - 1], x["c"][i - 1]) and min(x["o"][i], x["c"][i]) > min(x["o"][i - 1], x["c"][i - 1])


scan(6028, "bullish-harami", "Bullish harami", "Candlesticks", BULL)(lambda x, i: red(x, i - 1) and green(x, i) and _inside_body(x, i))
scan(6017, "bearish-harami", "Bearish harami", "Candlesticks", BEAR)(lambda x, i: green(x, i - 1) and red(x, i) and _inside_body(x, i))
scan(6020, "bullish-harami-cross", "Bullish harami cross", "Candlesticks", BULL)(lambda x, i: red(x, i - 1) and doji(x, i) and _inside_body(x, i))
scan(6024, "bearish-harami-cross", "Bearish harami cross", "Candlesticks", BEAR)(lambda x, i: green(x, i - 1) and doji(x, i) and _inside_body(x, i))
scan(6012, "piercing-line", "Piercing line", "Candlesticks", BULL)(
    lambda x, i: red(x, i - 1) and green(x, i) and x["o"][i] < x["c"][i - 1] and (x["o"][i - 1] + x["c"][i - 1]) / 2 < x["c"][i] < x["o"][i - 1])
scan(6016, "darkcloud-cover", "Dark cloud cover", "Candlesticks", BEAR)(
    lambda x, i: green(x, i - 1) and red(x, i) and x["o"][i] > x["c"][i - 1] and x["o"][i - 1] < x["c"][i] < (x["o"][i - 1] + x["c"][i - 1]) / 2)
scan(6013, "neckline", "On-neck line", "Candlesticks", BEAR, "Green close within 0.5% of the prior red candle's low")(
    lambda x, i: red(x, i - 1) and green(x, i) and x["o"][i] < x["l"][i - 1] and near(x["c"][i], x["l"][i - 1], 0.5))
scan(6019, "morning-star", "Morning star", "Candlesticks", BULL)(
    lambda x, i: i >= 2 and red(x, i - 2) and body(x, i - 2) >= 0.6 * x["rng"][i - 2] and body(x, i - 1) <= 0.3 * body(x, i - 2)
    and max(x["o"][i - 1], x["c"][i - 1]) < x["c"][i - 2] and green(x, i) and x["c"][i] > (x["o"][i - 2] + x["c"][i - 2]) / 2)
scan(6022, "three-white-soldiers", "Three white soldiers", "Candlesticks", BULL)(
    lambda x, i: i >= 2 and all(green(x, j) and upper(x, j) <= 0.25 * x["rng"][j] for j in (i - 2, i - 1, i))
    and x["c"][i] > x["c"][i - 1] > x["c"][i - 2] and x["o"][i - 2] < x["o"][i - 1] < x["c"][i - 2] and x["o"][i - 1] < x["o"][i] < x["c"][i - 1])
scan(6010, "identical-three-crows", "Identical three crows", "Candlesticks", BEAR)(
    lambda x, i: i >= 2 and all(red(x, j) for j in (i - 2, i - 1, i)) and x["c"][i] < x["c"][i - 1] < x["c"][i - 2]
    and near(x["o"][i - 1], x["c"][i - 2], 0.3) and near(x["o"][i], x["c"][i - 1], 0.3))
scan(6027, "abandoned-baby-top", "Abandoned baby top", "Candlesticks", BEAR)(
    lambda x, i: i >= 2 and green(x, i - 2) and doji(x, i - 1) and x["l"][i - 1] > x["h"][i - 2] and red(x, i) and x["h"][i] < x["l"][i - 1])
scan(6023, "upside-tasuki-gap", "Upside tasuki gap", "Candlesticks", BULL)(
    lambda x, i: i >= 2 and green(x, i - 2) and green(x, i - 1) and x["l"][i - 1] > x["h"][i - 2] and red(x, i)
    and min(x["o"][i - 1], x["c"][i - 1]) < x["o"][i] < max(x["o"][i - 1], x["c"][i - 1]) and x["h"][i - 2] < x["c"][i] < x["l"][i - 1])
scan(6025, "downside-tasuki-gap", "Downside tasuki gap", "Candlesticks", BEAR)(
    lambda x, i: i >= 2 and red(x, i - 2) and red(x, i - 1) and x["h"][i - 1] < x["l"][i - 2] and green(x, i)
    and min(x["o"][i - 1], x["c"][i - 1]) < x["o"][i] < max(x["o"][i - 1], x["c"][i - 1]) and x["h"][i - 1] < x["c"][i] < x["l"][i - 2])


# ----------------------------------------------------------------------------- weekly (8000s)
def _w(x):
    return x["W"], len(x["W"]["c"]) - 1


scan(8001, "weekly-rsi-bullish", "Weekly RSI bullish", "Weekly", BULL, "Weekly RSI(14) crossed above 50 (current week partial)")(lambda x, i: cross_up(_w(x)[0]["rsi"], 50.0, _w(x)[1]))
scan(8002, "weekly-rsi-trending-up", "Weekly RSI trending up", "Weekly", BULL)(lambda x, i: rising(_w(x)[0]["rsi"], _w(x)[1], 3))
scan(8003, "weekly-rsi-crossed-above-70", "Weekly RSI crossed above 70", "Weekly", BULL)(lambda x, i: cross_up(_w(x)[0]["rsi"], 70.0, _w(x)[1]))
scan(8004, "weekly-rsi-bearish", "Weekly RSI bearish", "Weekly", BEAR)(lambda x, i: cross_dn(_w(x)[0]["rsi"], 50.0, _w(x)[1]))
scan(8005, "weekly-rsi-trending-down", "Weekly RSI trending down", "Weekly", BEAR)(lambda x, i: falling(_w(x)[0]["rsi"], _w(x)[1], 3))
scan(8006, "weekly-rsi-crossed-below-30", "Weekly RSI crossed below 30", "Weekly", BEAR)(lambda x, i: cross_dn(_w(x)[0]["rsi"], 30.0, _w(x)[1]))
scan(8007, "weekly-rsi-overbought-zone", "Weekly RSI overbought zone", "Weekly", NEUTRAL)(lambda x, i: ok(_w(x)[0]["rsi"][_w(x)[1]]) and _w(x)[0]["rsi"][_w(x)[1]] >= 70)
scan(8008, "weekly-rsi-oversold-zone", "Weekly RSI oversold zone", "Weekly", NEUTRAL)(lambda x, i: ok(_w(x)[0]["rsi"][_w(x)[1]]) and _w(x)[0]["rsi"][_w(x)[1]] <= 30)
scan(8101, "weekly-psar-indicating-bullish-reversal", "Weekly PSAR bullish reversal", "Weekly", BULL)(
    lambda x, i: _w(x)[1] >= 1 and _w(x)[0]["psar_up"][_w(x)[1]] is True and _w(x)[0]["psar_up"][_w(x)[1] - 1] is False)
scan(8102, "weekly-psar-indicating-bearish-reversal", "Weekly PSAR bearish reversal", "Weekly", BEAR)(
    lambda x, i: _w(x)[1] >= 1 and _w(x)[0]["psar_up"][_w(x)[1]] is False and _w(x)[0]["psar_up"][_w(x)[1] - 1] is True)
scan(8201, "weekly-supertrend-signal-changed-to-buy", "Weekly Supertrend changed to buy", "Weekly", BULL)(
    lambda x, i: _w(x)[1] >= 1 and _w(x)[0]["st_up"][_w(x)[1]] is True and _w(x)[0]["st_up"][_w(x)[1] - 1] is False)
scan(8202, "weekly-supertrend-signal-changed-to-sell", "Weekly Supertrend changed to sell", "Weekly", BEAR)(
    lambda x, i: _w(x)[1] >= 1 and _w(x)[0]["st_up"][_w(x)[1]] is False and _w(x)[0]["st_up"][_w(x)[1] - 1] is True)
scan(8203, "price-nearing-weekly-supertrend-support", "Price nearing weekly Supertrend support", "Weekly", BULL)(
    lambda x, i: _w(x)[0]["st_up"][_w(x)[1]] is True and near(x["c"][i], _w(x)[0]["st"][_w(x)[1]], 2.0))
scan(8204, "price-nearing-weekly-supertrend-resistance", "Price nearing weekly Supertrend resistance", "Weekly", BEAR)(
    lambda x, i: _w(x)[0]["st_up"][_w(x)[1]] is False and near(x["c"][i], _w(x)[0]["st"][_w(x)[1]], 2.0))
for _kind, base in (("sma", 8400), ("ema", 8500)):
    for k, p in enumerate((5, 10, 20, 50)):
        key = f"{_kind}{p}"
        K = _kind.upper()
        scan(base + 1 + k, f"close-near-{p}-week-{_kind}", f"Close near {p}-week {K}", "Weekly", NEUTRAL, "Within 1%")(
            (lambda kk: lambda x, i: near(_w(x)[0]["c"][_w(x)[1]], _w(x)[0][kk][_w(x)[1]], 1.0))(key))
        scan(base + 5 + 2 * k, f"close-crossing-{p}-week-{_kind}-from-below", f"Close crossing {p}-week {K} from below", "Weekly", BULL)(
            (lambda kk: lambda x, i: cross_up(_w(x)[0]["c"], _w(x)[0][kk], _w(x)[1]))(key))
        scan(base + 6 + 2 * k, f"close-crossing-{p}-week-{_kind}-from-above", f"Close crossing {p}-week {K} from above", "Weekly", BEAR)(
            (lambda kk: lambda x, i: cross_dn(_w(x)[0]["c"], _w(x)[0][kk], _w(x)[1]))(key))
for _sid, _slug, _name, _cond, _bias in (
    (8301, "strongly-outperforming-benchmark-index-21-weeks", "Strongly outperforming NIFTY 50 (21 weeks)", lambda r: r > 0.10, BULL),
    (8302, "strongly-underperforming-benchmark-index-21-weeks", "Strongly underperforming NIFTY 50 (21 weeks)", lambda r: r < -0.10, BEAR),
):
    scan(_sid, _slug, _name, "Weekly", _bias, "Relative strength > +10% / < −10%")(
        (lambda cond: lambda x, i: ok(_w(x)[0]["rs21"][_w(x)[1]]) and cond(_w(x)[0]["rs21"][_w(x)[1]]))(_cond))
scan(8303, "increasing-relative-strength-benchmark-index-21-weeks", "Increasing relative strength (21 weeks)", "Weekly", BULL)(lambda x, i: rising(_w(x)[0]["rs21"], _w(x)[1], 3))
scan(8304, "decreasing-relative-strength-benchmark-index-21-weeks", "Decreasing relative strength (21 weeks)", "Weekly", BEAR)(lambda x, i: falling(_w(x)[0]["rs21"], _w(x)[1], 3))
scan(8305, "relative-strength-going-above-zero-benchmark-index-21-weeks", "Relative strength going above zero (21 weeks)", "Weekly", BULL)(lambda x, i: cross_up(_w(x)[0]["rs21"], 0.0, _w(x)[1]))
scan(8306, "relative-strength-going-below-zero-benchmark-index-21-weeks", "Relative strength going below zero (21 weeks)", "Weekly", BEAR)(lambda x, i: cross_dn(_w(x)[0]["rs21"], 0.0, _w(x)[1]))


# ----------------------------------------------------------------------------- fundamentals (7000s, only with data/fundamentals.csv)
def _f(key):
    return lambda x, i: x.get("fund", {}).get(key)


FUND_SCANS = [
    (7404, "low-pe-0-to-10", "Low PE (0–10)", BULL, lambda f: f.get("pe") is not None and 0 < f["pe"] <= 10),
    (7403, "moderate-pe-10-to-20", "Moderate PE (10–20)", NEUTRAL, lambda f: f.get("pe") is not None and 10 < f["pe"] <= 20),
    (7402, "high-pe-20-to-50", "High PE (20–50)", NEUTRAL, lambda f: f.get("pe") is not None and 20 < f["pe"] <= 50),
    (7401, "very-high-pe-50", "Very high PE (>50)", BEAR, lambda f: f.get("pe") is not None and f["pe"] > 50),
    (7406, "pe-below-industry", "PE below industry", BULL, lambda f: f.get("pe") is not None and f.get("ind_pe") and f["pe"] < f["ind_pe"]),
    (7405, "pe-above-industry", "PE above industry", BEAR, lambda f: f.get("pe") is not None and f.get("ind_pe") and f["pe"] > f["ind_pe"]),
    (7408, "price-below-book-value", "Price below book value", BULL, lambda f: f.get("pb") is not None and f["pb"] < 1),
    (7407, "price-above-book-value", "Price above book value", NEUTRAL, lambda f: f.get("pb") is not None and f["pb"] >= 1),
    (7502, "positive-dividend-yield", "Positive dividend yield", BULL, lambda f: (f.get("dy") or 0) > 0),
    (7001, "high-return-on-equity", "High return on equity", BULL, lambda f: (f.get("roe") or 0) >= 20),
    (7003, "high-return-on-capital-employed", "High return on capital employed", BULL, lambda f: (f.get("roce") or 0) >= 20),
    (7201, "no-leverage", "No leverage", BULL, lambda f: f.get("de") is not None and f["de"] <= 0.01),
    (7202, "low-leverage", "Low leverage", BULL, lambda f: f.get("de") is not None and 0.01 < f["de"] <= 0.5),
    (7203, "moderate-leverage", "Moderate leverage", NEUTRAL, lambda f: f.get("de") is not None and 0.5 < f["de"] <= 1.5),
    (7204, "high-leverage", "High leverage", BEAR, lambda f: f.get("de") is not None and f["de"] > 1.5),
]
for _sid, _slug, _name, _bias, _cond in FUND_SCANS:
    scan(_sid, _slug, _name, "Fundamentals", _bias, "From data/fundamentals.csv")((lambda cond: lambda x, i: bool(x.get("fund")) and bool(cond(x["fund"])))(_cond))


# ----------------------------------------------------------------------------- evaluation
def evaluate(s: dict[str, list], bench_close: dict[str, float], fund: dict[str, Any] | None = None) -> list[int]:
    """IDs of every catalogue scan the latest bar matches."""
    if len(s["c"]) < 30:
        return []
    x = compute(s, bench_close)
    if fund:
        x["fund"] = fund
    i = len(x["c"]) - 1
    hits = []
    for sid, test in _TESTS.items():
        try:
            if test(x, i):
                hits.append(sid)
        except (ZeroDivisionError, IndexError, TypeError, ValueError):
            continue  # insufficient or degenerate data for this condition
    return sorted(hits)


def catalogue() -> list[dict[str, Any]]:
    return sorted(CATALOGUE, key=lambda r: (r["cat"], r["id"]))


def rating(hits: list[int]) -> dict[str, int]:
    bias = {r["id"]: r["bias"] for r in CATALOGUE}
    return {"bull": sum(1 for h in hits if bias.get(h) == BULL), "bear": sum(1 for h in hits if bias.get(h) == BEAR),
            "neutral": sum(1 for h in hits if bias.get(h) == NEUTRAL)}
