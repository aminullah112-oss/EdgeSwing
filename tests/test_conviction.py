import datetime as dt
import importlib.util
import json
import math
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / ".github" / "scripts"
sys.path.insert(0, str(SCRIPTS))
import conviction as conv  # noqa: E402

spec = importlib.util.spec_from_file_location("edge_market_conv", SCRIPTS / "build_market_data.py")
market = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(market)


def days(n):
    return [(dt.date(2025, 1, 1) + dt.timedelta(days=i)).isoformat() for i in range(n)]


def rows(symbol, closes, vol=2_000_000.0, up_vol=None):
    out = []
    for i, (d, c) in enumerate(zip(days(len(closes)), closes)):
        v = up_vol if up_vol and i and c > closes[i - 1] else vol
        out.append({"date": d, "symbol": symbol, "open": c * 0.995, "high": c * 1.01, "low": c * 0.985,
                    "close": c, "volume": v, "value": c * v})
    return out


def wave(n, base=100.0, drift=0.0, amp=0.03, period=9.0):
    return [base * (1 + drift) ** i * (1 + amp * math.sin(i / period)) for i in range(n)]


class IndicatorTests(unittest.TestCase):
    def test_adx_high_in_trend_low_in_chop(self):
        trend = [100 * 1.01 ** i for i in range(80)]
        chop = [100 + (1 if i % 2 else -1) for i in range(80)]
        t = conv.wilder_adx([c * 1.01 for c in trend], [c * 0.99 for c in trend], trend)
        ch = conv.wilder_adx([c + 1 for c in chop], [c - 1 for c in chop], chop)
        self.assertGreater(t[-1], 40)
        self.assertLess(ch[-1], 20)
        self.assertTrue(math.isnan(t[20]))

    def test_rs55_against_benchmark(self):
        r = rows("A", [100 * 1.004 ** i for i in range(120)])
        bench = {d: 100.0 for d in days(120)}  # flat index
        s = conv.extend(market.series(r), bench)
        self.assertGreater(s["rs55"][-1], 0.2)
        self.assertTrue(math.isnan(s["rs55"][10]))

    def test_vwap_fallback_uses_typical_price(self):
        r = rows("A", [100.0] * 30)
        for x in r:
            x["value"] = None
        s = conv.extend(market.series(r), {})
        self.assertAlmostEqual(s["vw20x"][-1], (101 + 98.5 + 100) / 3, places=6)


class FactorTests(unittest.TestCase):
    def test_strong_uptrend_scores_high(self):
        closes = [100 * 1.003 ** i * (1 + 0.02 * math.sin(i / 6)) for i in range(260)]
        r = rows("UP", closes, up_vol=3_000_000.0)
        s = conv.extend(market.series(r), {d: 100.0 for d in days(260)})
        f = conv.factors_at(s, 259)
        for key in ("trend", "rs55", "vwap", "leader", "volume", "adx"):
            self.assertTrue(f[key], key)
        self.assertGreaterEqual(conv.score(f), 6)

    def test_downtrend_scores_low(self):
        r = rows("DN", [100 * 0.997 ** i for i in range(260)])
        s = conv.extend(market.series(r), {d: 100.0 for d in days(260)})
        f = conv.factors_at(s, 259)
        self.assertFalse(f["trend"]) ; self.assertFalse(f["rs55"]) ; self.assertFalse(f["leader"])
        self.assertLessEqual(conv.score(f), 2)


class RegimeAndVerdictTests(unittest.TestCase):
    def test_market_regime(self):
        ds = days(60)
        up = {d: 100 + i for i, d in enumerate(ds)}
        down = {d: 200 - i for i, d in enumerate(ds)}
        self.assertTrue(conv.market_regime(up)[ds[-1]])
        self.assertFalse(conv.market_regime(down)[ds[-1]])
        self.assertNotIn(ds[10], conv.market_regime(up))

    def test_verdict_uses_regime_on_trade_days(self):
        def st(days, avg, t, excess=0.5):
            return {"trade": {"on": {"days": days, "avg_r": avg, "t_days": t}}, "h10": {"on": {"excess": excess}}}
        self.assertEqual(conv.verdict(st(40, 0.2, 2.5)), "supported")
        self.assertEqual(conv.verdict(st(40, 0.2, 2.5, excess=-0.1)), "weak")
        self.assertEqual(conv.verdict(st(40, 0.1, 1.2)), "weak")
        self.assertEqual(conv.verdict(st(40, -0.1, 0.5)), "not_supported")
        self.assertEqual(conv.verdict(st(10, 0.5, 4.0)), "insufficient")


class BacktestTests(unittest.TestCase):
    def test_event_study_counts_new_entries_and_uses_next_open(self):
        n = 240
        ds = days(n)
        bench = {d: 100 + 0.1 * i for i, d in enumerate(ds)}  # rising index: regime on
        series = {}
        for k in range(3):
            series[f"S{k}"] = conv.extend(market.series(rows(f"S{k}", wave(n, base=100 + k, drift=0.001))), bench)
        out = conv.backtest(series, set(), ds, bench)
        self.assertEqual(set(out["scans"]), {sc["id"] for sc in conv.HC_SCANS + conv.SCREENER_RULES})
        base = out["baseline"]["h10"]["all"]
        self.assertEqual(base["n"], 3 * (n - 1 - conv.WARMUP - 9))  # every eligible stock-day with a 10-day window
        self.assertEqual(out["regime_days"]["off"], 0)
        # a scan can never record more events than eligible stock-days
        for st in out["scans"].values():
            self.assertLessEqual(st["h10"]["all"]["n"], base["n"])

    def test_split_window_is_skipped_not_whole_stock(self):
        n = 480
        ds = days(n)
        bench = {d: 100.0 + i * 0.01 for i, d in enumerate(ds)}
        closes = wave(n)
        closes = closes[:300] + [c / 2 for c in closes[300:]]  # bonus-like halving at index 300
        s = conv.extend(market.series(rows("X", closes)), bench)
        out = conv.backtest({"X": s}, set(), ds, bench)
        clean = conv.backtest({"Y": conv.extend(market.series(rows("Y", wave(n))), bench)}, set(), ds, bench)
        got, full = out["baseline"]["h10"]["all"]["n"], clean["baseline"]["h10"]["all"]["n"]
        self.assertGreater(got, 0)                      # pre-split history still used
        # blocked: 15 before to 250 after the jump (285..550), clipped to the last day with a 10-day window (469)
        self.assertEqual(full - got, (480 - 11) - (300 - 15) + 1)


class TradeSimulationTests(unittest.TestCase):
    def series_from(self, bars):
        n = len(bars)
        return {"o": [b[0] for b in bars], "h": [b[1] for b in bars], "l": [b[2] for b in bars],
                "c": [b[3] for b in bars], "atr": [5.0] * n}

    def test_target_hit_is_two_r_minus_costs(self):
        # signal close 100, ATR 5 -> trigger 100.25, stop 90; next bar fills, then target 120.75 hit
        s = self.series_from([(100, 101, 99, 100), (100, 101, 99.5, 100.5), (101, 125, 100.5, 124)])
        outcome, r = conv.simulate_trade(s, 0)
        risk = 100.25 - 90
        self.assertEqual(outcome, "trade")
        self.assertAlmostEqual(r, ((100.25 + 2 * risk) / 100.25 - 1 - conv.ROUND_TRIP_COST) / (risk / 100.25), places=6)

    def test_stop_first_when_both_touch(self):
        s = self.series_from([(100, 101, 99, 100), (100, 101, 99.5, 100.5), (101, 125, 85, 100)])
        outcome, r = conv.simulate_trade(s, 0)
        self.assertLess(r, -1)  # -1R plus costs

    def test_gap_below_stop_fills_at_open(self):
        s = self.series_from([(100, 101, 99, 100), (100, 101, 99.5, 100.5), (80, 82, 79, 81)])
        _, r = conv.simulate_trade(s, 0)
        self.assertLess(r, -1.9)  # opened at 80, well through the 90 stop

    def test_no_trigger_expires(self):
        s = self.series_from([(100, 101, 99, 100)] + [(99, 100, 98, 99)] * 5)
        self.assertEqual(conv.simulate_trade(s, 0), ("expired", None))

    def test_rolling_extreme_matches_naive(self):
        import random
        rng = random.Random(1)
        vals = [rng.random() for _ in range(400)]
        for fn in (max, min):
            naive = [fn(vals[max(0, i - 49):i + 1]) for i in range(len(vals))]
            self.assertEqual(market.rolling_extreme(vals, 50, fn), naive)


class AnnotateTests(unittest.TestCase):
    def test_screen_candidates_get_conviction_and_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            snap = Path(tmp) / "snapshot.json"
            screen = Path(tmp) / "screen.json"
            cols = market.SNAPSHOT_COLUMNS
            row = {c: None for c in cols} | {"s": "ABC", "d": "2026-11-23", "cv": 6, "cvf": ["trend", "rs55"], "hc": ["hc_leaders"]}
            ev = {"period": ["2026-07-01", "2026-11-10"], "scans": {"scr_breakout": {"h10": {"on": {"n": 40}}, "verdict": "supported"}}}
            snap.write_text(json.dumps({"columns": cols, "rows": [[row[c] for c in cols]], "meta": {"conviction": {
                "factors": [{"id": "trend", "label": "T"}], "regime": {"on": True}, "evidence": ev}}}))
            screen.write_text(json.dumps({"candidates": [{"symbol": "ABC", "date": "2026-11-23"}, {"symbol": "OLD", "date": "2026-11-20"}]}))
            n = market.annotate_screen(snap, screen)
            out = json.loads(screen.read_text())
        self.assertEqual(n, 1)
        self.assertEqual(out["candidates"][0]["conviction"], 6)
        self.assertNotIn("conviction", out["candidates"][1])
        self.assertTrue(out["market_regime"]["on"])
        self.assertEqual(out["setup_evidence"]["breakout"]["verdict"], "supported")


if __name__ == "__main__":
    unittest.main()


class ArchiveTests(unittest.TestCase):
    def test_write_load_and_sync(self):
        import archive_nse as arch
        with tempfile.TemporaryDirectory() as tmp:
            original = arch.ARCHIVE_DIR
            arch.ARCHIVE_DIR = Path(tmp)
            try:
                hist = []
                for d in ["2026-08-28", "2026-08-31", "2026-09-01", "2026-09-30", "2026-10-01"]:
                    hist.append({"date": d, "symbol": "ABC", "open": 10.0, "high": 11.0, "low": 9.0,
                                 "close": 10.5, "volume": 100.0, "value": 1000.0})
                written = arch.sync_from_history(hist, today=dt.date(2026, 10, 15))
                self.assertEqual(written, ["2026-09"])  # Aug incomplete in history, Oct is current
                first = (Path(tmp) / "2026-09.csv.gz").read_bytes()
                arch.write_month("2026-09", [r for r in hist if r["date"].startswith("2026-09")])
                self.assertEqual(first, (Path(tmp) / "2026-09.csv.gz").read_bytes())  # deterministic gzip
                by = arch.load_by_symbol([{**hist[2], "close": 10.9}])
                self.assertEqual(sorted(by["ABC"]), ["2026-09-01", "2026-09-30"])
                self.assertEqual(by["ABC"]["2026-09-01"][3], 10.9)   # history overrides archive
                self.assertEqual(by["ABC"]["2026-09-30"][5], 1000.0)
            finally:
                arch.ARCHIVE_DIR = original
