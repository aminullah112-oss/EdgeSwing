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
spec = importlib.util.spec_from_file_location("edge_market_data", SCRIPTS / "build_market_data.py")
market = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(market)
core = market.core


def bars(symbol, closes, start_volume=100_000, jump_at=None):
    """Deterministic synthetic bars on consecutive calendar days."""
    import datetime as dt
    out = []
    day = dt.date(2025, 1, 1)
    for i, close in enumerate(closes):
        out.append({"date": (day + dt.timedelta(days=i)).isoformat(), "symbol": symbol,
                    "open": close * 0.995, "high": close * 1.01, "low": close * 0.985,
                    "close": close, "volume": float(start_volume + (i % 7) * 1000)})
    return out


def wave(n, base=100.0, drift=0.002, amp=0.03):
    return [base * (1 + drift) ** i * (1 + amp * math.sin(i / 5)) for i in range(n)]


class IndicatorParityTests(unittest.TestCase):
    """The explorer must report the same RSI/SMA/EMA/ATR as the existing screener."""

    def test_matches_pipeline_indicator_series(self):
        rows = bars("ABC", wave(260))
        ref = core.indicator_series(rows)
        s = market.series(rows)
        for i in (20, 60, 210, 259):
            for mine, theirs in (("s20", "sma20"), ("s50", "sma50"), ("s200", "sma200"),
                                 ("e20", "ema20"), ("rsi", "rsi"), ("atr", "atr")):
                a, b = s[mine][i], ref[i][theirs]
                if math.isnan(b):
                    self.assertTrue(math.isnan(a), (mine, i))
                else:
                    self.assertAlmostEqual(a, b, places=8, msg=(mine, i))

    def test_rolling_extreme_uses_trailing_window(self):
        self.assertEqual(market.rolling_extreme([1, 5, 2, 3], 2, max), [1, 5, 5, 3])


class EventTests(unittest.TestCase):
    def test_new_52_week_high_and_20_day_breakout(self):
        closes = wave(260, drift=0.0, amp=0.02) + [130.0]
        s = market.series(bars("UP", closes))
        ev = market.events_for(s)
        self.assertIn("h52", ev)
        self.assertIn("b20", ev)
        self.assertNotIn("l52", ev)

    def test_golden_cross_detected_within_lookback(self):
        closes = [100 - i * 0.2 for i in range(210)] + [58 + i * 3 for i in range(60)]
        s = market.series(bars("GX", closes))
        crosses = [i for i in range(1, len(closes)) if market.crossed(s["s50"], s["s200"], i, True)]
        self.assertTrue(crosses, "fixture should contain a golden cross")
        trimmed = market.series(bars("GX", closes[:crosses[0] + 2]))
        self.assertIn("gx", market.events_for(trimmed))

    def test_no_52_week_events_without_a_full_year(self):
        s = market.series(bars("NEW", wave(100) + [500.0]))
        self.assertNotIn("h52", market.events_for(s))


class SnapshotTests(unittest.TestCase):
    def test_returns_blank_across_corporate_action_jump(self):
        closes = wave(270)
        closes = closes[:250] + [c / 2 for c in closes[250:]]  # 1:1 bonus-like halving
        rows = bars("SPLIT", closes)
        flags = core.corporate_action_symbols({"SPLIT": rows})["SPLIT"]
        s = market.series(rows)
        row = dict(zip(market.SNAPSHOT_COLUMNS,
                       market.snapshot_row("SPLIT", s, {}, flags, rows[-1]["date"], "SPLIT")))
        self.assertEqual(row["ca"], rows[250]["date"])
        self.assertIsNone(row["r1m"])      # 21-session window spans the jump
        self.assertIsNotNone(row["r1w"])   # 5-session window is after it
        self.assertIsNone(row["h52"])

    def test_stock_keys_are_url_safe_and_unique(self):
        used = set()
        self.assertEqual(market.stock_key("M&M", used), "M_M")
        self.assertEqual(market.stock_key("M_M", used), "M_M_2")
        self.assertEqual(market.stock_key("BAJAJ-AUTO", used), "BAJAJ-AUTO")


class ConstituentTests(unittest.TestCase):
    CSV = (b"\xef\xbb\xbfCompany Name,Industry,Symbol,Series,ISIN Code\n"
           b"Reliance Industries Ltd.,Oil Gas & Consumable Fuels,RELIANCE,EQ,INE002A01018\n"
           b"Mahindra & Mahindra Ltd.,Automobile and Auto Components,M&M,EQ,INE101A01026\n")

    def test_parse(self):
        rows = market.parse_constituents(self.CSV)
        self.assertEqual([r["symbol"] for r in rows], ["RELIANCE", "M&M"])
        self.assertEqual(rows[1]["industry"], "Automobile and Auto Components")

    def test_bad_header_rejected(self):
        with self.assertRaises(ValueError):
            market.parse_constituents(b"foo,bar\n1,2\n")

    def test_fetch_failure_falls_back_to_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "index_constituents.csv"
            cache.write_text("symbol,name,industry,cap,nifty50\nRELIANCE,Reliance,Energy,large,1\n")
            original = market.CONSTITUENTS_CACHE
            market.CONSTITUENTS_CACHE = cache
            try:
                def boom(url):
                    raise OSError("blocked")
                mapping, source, note = market.fetch_constituents(fetch=boom)
            finally:
                market.CONSTITUENTS_CACHE = original
        self.assertEqual(source, "cached index constituent lists")
        self.assertTrue(mapping["RELIANCE"]["nifty50"])
        self.assertIn("blocked", note)

    def test_short_list_does_not_overwrite_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "index_constituents.csv"
            original = market.CONSTITUENTS_CACHE
            market.CONSTITUENTS_CACHE = cache
            try:
                _, source, note = market.fetch_constituents(fetch=lambda url: self.CSV)
            finally:
                market.CONSTITUENTS_CACHE = original
            self.assertFalse(cache.exists())
        self.assertEqual(source, "none")
        self.assertIn("only 2 constituents", note)


class BuildTests(unittest.TestCase):
    def test_end_to_end_build(self):
        history = (bars("AAA", wave(260)) + bars("M&M", wave(260, base=50, drift=-0.001))
                   + bars("NIFTY50", wave(260, base=20000)))
        info = {"AAA": {"name": "Alpha", "industry": "IT", "cap": "large", "nifty50": True}}
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "market"
            summary = market.build(history, info, out, "test", [])
            snap = json.loads((out / "snapshot.json").read_text())
            breadth = json.loads((out / "breadth.json").read_text())
            chart = json.loads((out / "stocks" / "M_M.json").read_text())
            self.assertTrue((out / "stocks" / "NIFTY50.json").exists())
        self.assertEqual(summary["symbols"], 2)
        rows = [dict(zip(snap["columns"], r)) for r in snap["rows"]]
        self.assertEqual({r["s"] for r in rows}, {"AAA", "M&M"})
        self.assertEqual(rows[0]["n"], "Alpha")
        self.assertEqual(rows[1]["ind"], "Unclassified")
        self.assertEqual(chart["s"], "M&M")
        self.assertEqual(len(chart["bars"]), 260)
        self.assertEqual(len(breadth["rows"]), 260)
        self.assertEqual(snap["benchmark"]["s"], "NIFTY50")


if __name__ == "__main__":
    unittest.main()
