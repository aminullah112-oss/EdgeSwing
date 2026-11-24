import datetime as dt
import importlib.util
import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / ".github" / "scripts"
sys.path.insert(0, str(SCRIPTS))
import se_scans as se  # noqa: E402

spec = importlib.util.spec_from_file_location("edge_market_se", SCRIPTS / "build_market_data.py")
market = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(market)


def days(n):
    out, d = [], dt.date(2025, 1, 6)
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += dt.timedelta(days=1)
    return out


def series(bars, value=True):
    """bars: list of (o, h, l, c, v)."""
    ds = days(len(bars))
    rows = [{"date": d, "symbol": "T", "open": o, "high": h, "low": l, "close": c, "volume": v,
             "value": (h + l + c) / 3 * v if value else None} for d, (o, h, l, c, v) in zip(ds, bars)]
    return market.series(rows), {d: 100.0 + i * 0.01 for i, d in enumerate(ds)}


def flat(n, px=100.0, vol=1000.0):
    return [(px, px * 1.01, px * 0.99, px, vol) for _ in range(n)]


class CatalogueTests(unittest.TestCase):
    def test_ids_unique_and_biases_valid(self):
        ids = [c["id"] for c in se.CATALOGUE]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertGreater(len(ids), 300)
        self.assertTrue(all(c["bias"] in (se.BULL, se.BEAR, se.NEUTRAL) for c in se.CATALOGUE))

    def test_every_scan_runs_on_varied_data_without_error(self):
        import random
        rng = random.Random(7)
        px, bars = 100.0, []
        for i in range(300):
            o = px * (1 + rng.uniform(-0.02, 0.02))
            c = o * (1 + rng.uniform(-0.03, 0.03))
            h, l = max(o, c) * (1 + rng.uniform(0, 0.02)), min(o, c) * (1 - rng.uniform(0, 0.02))
            bars.append((o, h, l, c, rng.uniform(500, 5000)))
            px = c
        for n in (30, 60, 300):
            s, b = series(bars[:n])
            x = se.compute(s, b)
            for sid, test in se._TESTS.items():
                with self.subTest(n=n, sid=sid):
                    test(x, n - 1)  # must not raise

    def test_short_history_returns_no_hits(self):
        s, b = series(flat(10))
        self.assertEqual(se.evaluate(s, b), [])


class DefinitionTests(unittest.TestCase):
    def hits(self, bars, **kw):
        s, b = series(bars, **kw)
        return set(se.evaluate(s, b))

    def test_52_week_high_cross_and_zone(self):
        bars = flat(260) + [(100, 115, 100, 114, 5000)]
        h = self.hits(bars)
        self.assertIn(2007, h)     # close crossing 52 week high
        self.assertIn(2019, h)     # within 52w high zone
        self.assertIn(1004, h)     # volume ≥ 2x average on an up day
        self.assertNotIn(2008, h)

    def test_nr7_and_breakout(self):
        wide = [(100, 103, 97, 100, 1000)] * 40
        bars = wide + [(100, 100.5, 99.5, 100, 1000)]
        self.assertIn(3242, self.hits(bars))                 # NR7 today
        self.assertIn(3245, self.hits(bars + [(100, 102, 99.9, 101.5, 1000)]))  # NR7 breakout next day

    def test_bullish_engulfing_and_opening_at_low(self):
        bars = flat(40) + [(101, 101.2, 98.8, 99, 1000), (98.5, 102, 98.5, 101.8, 1000)]
        h = self.hits(bars)
        self.assertIn(6007, h)
        self.assertIn(2010, h)   # open == low

    def test_supertrend_flip_on_breakdown(self):
        up = [(100 + i, 101.5 + i, 99.5 + i, 101 + i, 1000) for i in range(60)]
        crash = [(150, 150, 120, 121, 3000)]
        self.assertIn(3248, self.hits(up + crash))  # supertrend changed to sell

    def test_vwap_cross_uses_session_vwap(self):
        bars = flat(40)
        bars[-1] = (100, 101, 95, 96, 1000)              # close below typical-price VWAP
        bars.append((96, 104, 95.5, 103.5, 1000))         # close above
        self.assertIn(2221, self.hits(bars))
        self.assertNotIn(2221, self.hits(bars, value=False))  # no traded value -> no VWAP scans

    def test_weekly_aggregation(self):
        ds = days(10)
        keys, W = se.weekly(ds, [1] * 10, list(range(10)), [0] * 10, list(range(10)), [1] * 10)
        self.assertEqual(len(keys), 2)
        self.assertEqual(W["c"], [4, 9])
        self.assertEqual(W["v"], [5, 5])

    def test_fundamental_scans_need_fundamentals(self):
        s, b = series(flat(60))
        self.assertFalse(any(h >= 7000 and h < 8000 for h in se.evaluate(s, b)))
        hits = se.evaluate(s, b, {"pe": 8.0, "pb": 0.9, "dy": 2.0, "roe": 25.0, "roce": 30.0, "de": 0.0, "ind_pe": 20.0})
        for sid in (7404, 7406, 7408, 7502, 7001, 7003, 7201):
            self.assertIn(sid, hits)

    def test_rating_counts(self):
        r = se.rating([2007, 2008, 6001])
        self.assertEqual((r["bull"], r["bear"], r["neutral"]), (1, 1, 1))


if __name__ == "__main__":
    unittest.main()
