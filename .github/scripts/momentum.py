"""Monthly 12-1 momentum portfolio: current picks, backtest evidence and a frozen paper log.

Rules (all decided on data available at the signal close):
  * Rebalance on the last session of each month; trade at the next session's open.
  * Universe: 20-session average turnover >= Rs 5 crore, close >= Rs 20, at least 252 sessions of
    history, traded on >= 90% of the last 252 sessions. Delisted stocks are included in history.
  * Score (primary): 12-1 momentum = return from 252 to 21 sessions ago (skips the latest month).
  * Portfolio: top 30 by score, equal weight; 0.125% cost per side on traded weight.
Corporate actions: prices are unadjusted, so a one-day close move of 40% or more (NSE circuit bands
make genuine moves this large rare for liquid stocks) is treated as a 0% day when chaining returns.

Benchmarks: NIFTY 50 price index (no dividends) and an equal-weight portfolio of the whole universe
(the honest test: momentum must beat owning every eligible stock).

Paper log: from PAPER_START, each month's portfolio is written once to
data/momentum/portfolios/YYYY-MM.json when formed and never rewritten, so the forward record
cannot be recomputed after the fact.
"""
from __future__ import annotations

import json
import math
import statistics as st
from array import array
from collections import defaultdict
from pathlib import Path
from typing import Any

COST_SIDE = 0.00125
MIN_TURN, MIN_PX, LOOKBACK, SKIP = 5e7, 20.0, 252, 21
TOP_N = 30
RF = 0.065
PAPER_START = "2026-11-25"   # portfolios formed on or after this date are frozen (forward record)
NAN = float("nan")


class Stock:
    """Daily series aligned to the trading calendar from the stock's first session onward."""
    __slots__ = ("start", "adj", "adjo", "px", "traded", "turn_cum", "trd_cum", "lr_cum", "lr2_cum", "end")

    def __init__(self, start: int, length: int):
        self.start = start
        self.end = start + length - 1
        self.adj, self.adjo, self.px = array("d", [NAN] * length), array("d", [NAN] * length), array("d", [NAN] * length)
        self.traded = bytearray(length)

    def at(self, arr, k):
        j = k - self.start
        return arr[j] if 0 <= j < len(arr) else NAN

    def finish(self, turn: list[float]):
        n = len(self.adj)
        self.turn_cum, self.trd_cum = array("d", [0.0] * (n + 1)), array("d", [0.0] * (n + 1))
        self.lr_cum, self.lr2_cum = array("d", [0.0] * (n + 1)), array("d", [0.0] * (n + 1))
        prev = NAN
        for j in range(n):
            self.turn_cum[j + 1] = self.turn_cum[j] + (turn[j] if self.traded[j] else 0.0)
            self.trd_cum[j + 1] = self.trd_cum[j] + self.traded[j]
            lr = 0.0
            if self.traded[j] and not math.isnan(prev) and prev > 0:
                lr = math.log(self.adj[j] / prev)
            if self.traded[j]:
                prev = self.adj[j]
            self.lr_cum[j + 1] = self.lr_cum[j] + lr
            self.lr2_cum[j + 1] = self.lr2_cum[j] + lr * lr

    def last_valid(self, arr, k, back=10):
        for kk in range(k, k - back, -1):
            v = self.at(arr, kk)
            if not math.isnan(v):
                return v
        return NAN


def build_panel(data: dict[str, dict[str, tuple]], calendar: list[str]) -> dict[str, Stock]:
    cidx = {d: k for k, d in enumerate(calendar)}
    panel: dict[str, Stock] = {}
    for sym, bars in data.items():
        if sym.startswith("NIFTY"):
            continue
        days = sorted(d for d in bars if d in cidx)
        if len(days) < 30:
            continue
        k0 = cidx[days[0]]
        s = Stock(k0, cidx[days[-1]] - k0 + 1)
        turn = [0.0] * len(s.adj)
        level, prev_c = 1.0, None
        for d in days:
            o, h, l, c, v, _ = bars[d]
            if prev_c:
                r = c / prev_c - 1
                if abs(r) < 0.4:
                    level *= 1 + r
            j = cidx[d] - k0
            f = level / c
            s.adj[j], s.adjo[j], s.px[j], s.traded[j] = level, o * f, c, 1
            turn[j] = c * v
            prev_c = c
        s.finish(turn)
        panel[sym] = s
    return panel


def eligible(s: Stock, k: int) -> bool:
    j = k - s.start
    if j < LOOKBACK - 1 or j >= len(s.adj) or not s.traded[j] or s.px[j] < MIN_PX:
        return False
    if s.trd_cum[j + 1] - s.trd_cum[j + 1 - LOOKBACK] < 0.9 * LOOKBACK:
        return False
    days20 = s.trd_cum[j + 1] - s.trd_cum[j - 19]
    return days20 >= 15 and (s.turn_cum[j + 1] - s.turn_cum[j - 19]) / days20 >= MIN_TURN


def score_12_1(s: Stock, k: int) -> float:
    a, b = s.last_valid(s.adj, k - SKIP), s.last_valid(s.adj, k - LOOKBACK)
    return a / b - 1 if a > 0 and b > 0 else NAN


def score_6_1(s: Stock, k: int) -> float:
    a, b = s.last_valid(s.adj, k - SKIP), s.last_valid(s.adj, k - 126)
    return a / b - 1 if a > 0 and b > 0 else NAN


def vol_252(s: Stock, k: int) -> float:
    j = k - s.start
    n = s.trd_cum[j + 1] - s.trd_cum[j + 1 - LOOKBACK]
    if n < 200:
        return NAN
    m1 = (s.lr_cum[j + 1] - s.lr_cum[j + 1 - LOOKBACK]) / n
    m2 = (s.lr2_cum[j + 1] - s.lr2_cum[j + 1 - LOOKBACK]) / n
    return math.sqrt(max(0.0, m2 - m1 * m1)) * math.sqrt(252)


def nse_scores(panel: dict[str, Stock], syms: list[str], k: int) -> dict[str, float]:
    """NIFTY momentum-index style: mean z-score of 6m and 12m return divided by 1-year volatility."""
    parts = {}
    for sym in syms:
        s = panel[sym]
        now, p6, p12, vol = s.last_valid(s.adj, k), s.last_valid(s.adj, k - 126), s.last_valid(s.adj, k - LOOKBACK + 1), vol_252(s, k)
        if now > 0 and p6 > 0 and p12 > 0 and vol > 0:
            parts[sym] = ((now / p6 - 1) / vol, (now / p12 - 1) / vol)
    if len(parts) < 10:
        return {}
    out = defaultdict(float)
    for i in (0, 1):
        vals = [p[i] for p in parts.values()]
        mu, sd = st.fmean(vals), st.pstdev(vals) or 1.0
        for sym, p in parts.items():
            out[sym] += 0.5 * (p[i] - mu) / sd
    return dict(out)


def is_month_end_session(date: str) -> bool:
    """True when the next weekday falls in a new month (a holiday on that weekday delays recognition by a day)."""
    import datetime as dt
    d = dt.date.fromisoformat(date) + dt.timedelta(days=1)
    while d.weekday() >= 5:
        d += dt.timedelta(days=1)
    return d.month != dt.date.fromisoformat(date).month


def month_ends(calendar: list[str]) -> list[int]:
    ends = [k for k in range(len(calendar) - 1) if calendar[k][:7] != calendar[k + 1][:7] and k >= LOOKBACK]
    if calendar and is_month_end_session(calendar[-1]) and len(calendar) - 1 >= LOOKBACK:
        ends.append(len(calendar) - 1)  # latest session closes the month: form the next portfolio tonight
    return ends


def pick(panel: dict[str, Stock], k: int, strategy: str, top_n: int) -> tuple[list[str], list[str], dict[str, float]]:
    uni = [sym for sym, s in panel.items() if eligible(s, k)]
    if strategy == "universe":
        return uni, uni, {}
    if strategy == "nse":
        sc = nse_scores(panel, uni, k)
    else:
        fn = score_12_1 if strategy == "12-1" else score_6_1
        sc = {sym: fn(panel[sym], k) for sym in uni}
        sc = {sym: v for sym, v in sc.items() if not math.isnan(v)}
    return sorted(sc, key=lambda x: -sc[x])[:top_n], uni, sc


def period_return(s: Stock, k0: int, k1: int, last: int) -> float:
    """Buy at the open after k0, sell at the open after k1 (or the last available close)."""
    e = s.at(s.adjo, k0 + 1)
    if math.isnan(e):
        return NAN
    x = s.at(s.adjo, k1 + 1) if k1 + 1 <= last else NAN
    if math.isnan(x):
        for kk in range(min(k1, last), k0, -1):
            v = s.at(s.adj, kk)
            if not math.isnan(v):
                x = v
                break
    return x / e - 1 if x > 0 else NAN


def run_strategy(panel, calendar, nifty_close, ends, strategy, top_n, regime_filter=False):
    last = len(calendar) - 1
    dates, rets, turnover, prev = [], [], [], {}
    for m, k0 in enumerate(ends):
        k1 = ends[m + 1] if m + 1 < len(ends) else last - 1
        if k1 <= k0:
            break
        if regime_filter and not (k0 >= 199 and nifty_close[k0] > sum(nifty_close[k0 - 199:k0 + 1]) / 200):
            picks = []
        else:
            picks = pick(panel, k0, strategy, top_n)[0]
        w = {sym: 1 / len(picks) for sym in picks} if picks else {}
        traded = sum(abs(w.get(x, 0) - prev.get(x, 0)) for x in set(w) | set(prev))
        pr = [period_return(panel[x], k0, k1, last) for x in picks]
        pr = [r for r in pr if not math.isnan(r)]
        rets.append((st.fmean(pr) if pr else 0.0) - traded * COST_SIDE)
        dates.append(calendar[k0 + 1][:7])
        turnover.append(traded / 2)
        prev = w
    return dates, rets, turnover


def summarize(name, dates, rets, turnover=None, bench=None):
    eq, peak, mdd, curve = 1.0, 1.0, 0.0, []
    for d, r in zip(dates, rets):
        eq *= 1 + r
        peak = max(peak, eq)
        mdd = min(mdd, eq / peak - 1)
        curve.append([d, round(eq, 4)])
    years = len(rets) / 12 if rets else 1
    cagr = eq ** (1 / years) - 1 if eq > 0 else -1.0
    vol = st.pstdev(rets) * math.sqrt(12) if len(rets) > 1 else NAN
    out: dict[str, Any] = {"name": name, "cagr": round(cagr * 100, 2), "vol": round(vol * 100, 2),
                           "sharpe": round((cagr - RF) / vol, 2) if vol else None, "mdd": round(mdd * 100, 2),
                           "total": round((eq - 1) * 100, 1), "curve": curve}
    by_year: dict[str, float] = defaultdict(lambda: 1.0)
    for d, r in zip(dates, rets):
        by_year[d[:4]] *= 1 + r
    out["by_year"] = {y: round((v - 1) * 100, 1) for y, v in sorted(by_year.items())}
    if turnover:
        out["turnover"] = round(st.fmean(turnover) * 100, 1)
    if bench:
        ex = [a - b for a, b in zip(rets, bench)]
        se = st.stdev(ex) / math.sqrt(len(ex)) if len(ex) > 2 else NAN
        out.update(excess=round(st.fmean(ex) * 12 * 100, 2), t=round(st.fmean(ex) / se, 2) if se else None,
                   beat=round(sum(1 for e in ex if e > 0) / len(ex) * 100, 1))
    return out


def evidence(panel, calendar, nifty_close) -> dict[str, Any]:
    ends = month_ends(calendar)
    d_u, r_u, t_u = run_strategy(panel, calendar, nifty_close, ends, "universe", 0)
    last = len(calendar) - 1
    r_n = []
    for m, k0 in enumerate(ends[:len(r_u)]):
        k1 = ends[m + 1] if m + 1 < len(ends) else last - 1
        r_n.append(nifty_close[k1] / nifty_close[k0] - 1)
    variants = [summarize("NIFTY 50 (price index)", d_u, r_n), summarize("Equal-weight universe", d_u, r_u, t_u)]
    specs = [("12-1", 30, False, "12-1 momentum, top 30 (primary)"), ("12-1", 30, True, "12-1 top 30, only when NIFTY > 200-day SMA"),
             ("12-1", 20, False, "12-1 momentum, top 20"), ("nse", 30, False, "NSE-style risk-adjusted, top 30"),
             ("6-1", 20, False, "6-1 momentum, top 20")]
    for strat, n, rf, label in specs:
        d, r, t = run_strategy(panel, calendar, nifty_close, ends, strat, n, rf)
        variants.append({"id": f"{strat}-{n}{'-regime' if rf else ''}", **summarize(label, d, r, t, r_u)})
    # robustness: same primary rule rebalanced 10 sessions after month end
    mid = [k + 10 for k in ends if k + 10 < last - 1]
    d_um, r_um, _ = run_strategy(panel, calendar, nifty_close, mid, "universe", 0)
    d_m, r_m, t_m = run_strategy(panel, calendar, nifty_close, mid, "12-1", TOP_N)
    robust = summarize("12-1 top 30, rebalanced mid-month", d_m, r_m, t_m, r_um)
    robust["universe_cagr"] = summarize("u", d_um, r_um)["cagr"]
    return {"period": [d_u[0], d_u[-1]] if d_u else [None, None], "months": len(r_u), "variants": variants, "robustness": robust}


def current_portfolio(panel, calendar, info: dict[str, dict[str, Any]]) -> dict[str, Any]:
    ends = month_ends(calendar)
    if not ends:
        return {}
    k = ends[-1]
    picks, uni, sc = pick(panel, k, "12-1", TOP_N)
    prev_picks = pick(panel, ends[-2], "12-1", TOP_N)[0] if len(ends) > 1 else []
    last = len(calendar) - 1
    # "if rebalanced today": ranking on the latest close (watch only)
    today_picks, _, today_sc = pick(panel, last, "12-1", TOP_N)
    today_rank = {sym: i + 1 for i, sym in enumerate(sorted(today_sc, key=lambda x: -today_sc[x]))}
    rows = []
    for rank, sym in enumerate(picks, 1):
        s = panel[sym]
        r = period_return(s, k, last, last) if k + 1 <= last else NAN
        rows.append({"rank": rank, "s": sym, "n": info.get(sym, {}).get("name", ""), "ind": info.get(sym, {}).get("industry", ""),
                     "cap": info.get(sym, {}).get("cap", ""), "score": round(sc[sym] * 100, 1),
                     "r6_1": round(score_6_1(s, k) * 100, 1) if not math.isnan(score_6_1(s, k)) else None,
                     "vol": round(vol_252(s, k) * 100, 1) if not math.isnan(vol_252(s, k)) else None,
                     "close": round(s.last_valid(s.px, last), 2), "since": round(r * 100, 2) if not math.isnan(r) else None,
                     "rank_today": today_rank.get(sym)})
    return {"formed_on": calendar[k], "effective_from": calendar[k + 1] if k + 1 <= last else None, "as_of": calendar[last],
            "universe": len(uni), "picks": rows, "adds": [x for x in picks if x not in prev_picks],
            "drops": [x for x in prev_picks if x not in picks],
            "watch_today": {"adds": [x for x in today_picks if x not in picks], "drops": [x for x in picks if x not in today_picks]}}


def paper_log(panel, calendar, nifty_close, folder: Path, portfolio: dict[str, Any]) -> list[dict[str, Any]]:
    """Freeze the current month's portfolio (once) and report every frozen portfolio's realized return."""
    folder.mkdir(parents=True, exist_ok=True)
    if portfolio and portfolio["formed_on"] >= PAPER_START:
        path = folder / f"{portfolio['formed_on'][:7]}.json"
        if not path.exists():
            path.write_text(json.dumps({"formed_on": portfolio["formed_on"], "rule": "12-1 momentum, top 30, equal weight",
                                        "symbols": [p["s"] for p in portfolio["picks"]]}, indent=1) + "\n", encoding="utf-8")
    cidx = {d: k for k, d in enumerate(calendar)}
    ends = month_ends(calendar)
    last = len(calendar) - 1
    out = []
    for path in sorted(folder.glob("*.json")):
        frozen = json.loads(path.read_text(encoding="utf-8"))
        k0 = cidx.get(frozen["formed_on"])
        if k0 is None:
            continue
        nxt = [k for k in ends if k > k0]
        k1, complete = (nxt[0], True) if nxt else (last, False)
        rets = [period_return(panel[x], k0, k1, last) for x in frozen["symbols"] if x in panel]
        rets = [r for r in rets if not math.isnan(r)]
        uni = [x for x, s in panel.items() if eligible(s, k0)]
        ur = [period_return(panel[x], k0, k1, last) for x in uni]
        ur = [r for r in ur if not math.isnan(r)]
        out.append({"month": frozen["formed_on"][:7], "formed_on": frozen["formed_on"], "complete": complete,
                    "through": calendar[min(k1 + 1, last) if complete else last],
                    "portfolio": round(st.fmean(rets) * 100, 2) if rets else None,
                    "universe": round(st.fmean(ur) * 100, 2) if ur else None,
                    "nifty": round((nifty_close[k1] / nifty_close[k0] - 1) * 100, 2), "n": len(rets)})
    return out


def run(data: dict[str, dict[str, tuple]], info: dict[str, dict[str, Any]], folder: Path) -> dict[str, Any] | None:
    nifty = data.get("NIFTY50", {})
    calendar = sorted(nifty)
    if len(calendar) < LOOKBACK + 40:
        return None
    nifty_close = [nifty[d][3] for d in calendar]
    panel = build_panel(data, calendar)
    portfolio = current_portfolio(panel, calendar, info)
    return {"rules": {"score": "12-1 momentum: return from 252 to 21 sessions ago", "top_n": TOP_N, "rebalance": "last session of each month; trade next open",
                      "universe": "20-session avg turnover >= Rs 5 cr, close >= Rs 20, >= 252 sessions history, traded >= 90% of last 252 sessions",
                      "cost_per_side_pct": COST_SIDE * 100, "paper_start": PAPER_START},
            "portfolio": portfolio, "evidence": evidence(panel, calendar, nifty_close),
            "paper": paper_log(panel, calendar, nifty_close, folder, portfolio)}
