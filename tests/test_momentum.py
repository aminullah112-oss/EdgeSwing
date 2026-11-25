import datetime as dt
import json
import math
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".github" / "scripts"))
import momentum as mom  # noqa: E402


def sessions(n, start=dt.date(2023, 1, 2)):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += dt.timedelta(days=1)
    return out


def make_data(n=420, growth=None, vol=2_000_000.0):
    """growth: symbol -> daily growth rate. Prices start at 100; value is close * volume."""
    cal = sessions(n)
    data = {"NIFTY50": {d: (100 + i * 0.01,) * 4 + (0.0, None) for i, d in enumerate(cal)}}
    for sym, g in (growth or {}).items():
        bars = {}
        for i, d in enumerate(cal):
            c = 100 * (1 + g) ** i
            bars[d] = (c, c * 1.01, c * 0.99, c, vol, c * vol)
        data[sym] = bars
    return cal, data


class RuleTests(unittest.TestCase):
    def test_picks_strongest_trends_and_excludes_illiquid(self):
        growth = {f"S{i:02d}": 0.0002 * i for i in range(40)}
        cal, data = make_data(growth=growth)
        data["THIN"] = {d: (b[0] * 3, b[1] * 3, b[2] * 3, b[3] * 3, 10.0, None) for d, b in data["S39"].items()}  # strongest but illiquid
        panel = mom.build_panel(data, cal)
        k = len(cal) - 5
        picks, uni, sc = mom.pick(panel, k, "12-1", 30)
        self.assertNotIn("THIN", uni)
        self.assertEqual(picks[0], "S39")
        self.assertEqual(set(picks), {f"S{i:02d}" for i in range(10, 40)})

    def test_split_day_is_neutralised(self):
        cal, data = make_data(n=300, growth={"A": 0.0})
        split_at = cal[200]
        data["A"] = {d: tuple(x / 2 if (d >= split_at and j < 4) else (x / 2 if d >= split_at and j == 5 and x else x)
                              for j, x in enumerate(b)) for d, b in data["A"].items()}
        panel = mom.build_panel(data, cal)
        s = panel["A"]
        self.assertAlmostEqual(s.adj[250] / s.adj[150], 1.0)  # the 50% "drop" is a split, not a loss

    def test_period_return_next_open_and_delisting(self):
        cal, data = make_data(n=300, growth={"A": 0.001})
        panel = mom.build_panel(data, cal)
        r = mom.period_return(panel["A"], 260, 280, len(cal) - 1)
        self.assertAlmostEqual(r, (1.001 ** 281) / (1.001 ** 261) - 1, places=9)  # opens == closes in fixture
        del_cal = cal[:270]
        data["B"] = {d: b for d, b in data["A"].items() if d in del_cal}  # stops trading at index 269
        panel = mom.build_panel(data, cal)
        r = mom.period_return(panel["B"], 260, 290, len(cal) - 1)
        self.assertAlmostEqual(r, (1.001 ** 269) / (1.001 ** 261) - 1, places=9)  # exits at last available close

    def test_month_end_detection(self):
        self.assertTrue(mom.is_month_end_session("2026-11-30"))
        self.assertTrue(mom.is_month_end_session("2026-10-30"))    # Friday; next weekday is 2 Nov
        self.assertFalse(mom.is_month_end_session("2026-11-25"))

    def test_universe_benchmark_and_costs(self):
        growth = {f"S{i:02d}": 0.0003 for i in range(35)}
        cal, data = make_data(n=400, growth=growth)
        panel = mom.build_panel(data, cal)
        nc = [data["NIFTY50"][d][3] for d in cal]
        ends = mom.month_ends(cal)
        _, r_u, _ = mom.run_strategy(panel, cal, nc, ends, "universe", 0)
        _, r_m, t_m = mom.run_strategy(panel, cal, nc, ends, "12-1", 30)
        self.assertGreater(t_m[0], 0)          # first month buys everything
        self.assertLess(r_m[0], r_u[0] + 1e-12)  # identical stocks: momentum can only lose costs vs the universe


class PaperLogTests(unittest.TestCase):
    def test_frozen_once_and_reported(self):
        growth = {f"S{i:02d}": 0.0002 * i for i in range(35)}
        cal, data = make_data(n=420, growth=growth)
        panel = mom.build_panel(data, cal)
        nc = [data["NIFTY50"][d][3] for d in cal]
        ends = mom.month_ends(cal)
        k = ends[-2]
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            original = mom.PAPER_START
            mom.PAPER_START = "2000-01-01"
            try:
                portfolio = {"formed_on": cal[k], "picks": [{"s": "S34"}, {"s": "S33"}]}
                log = mom.paper_log(panel, cal, nc, folder, portfolio)
                saved = json.loads((folder / f"{cal[k][:7]}.json").read_text())
                mom.paper_log(panel, cal, nc, folder, {"formed_on": cal[k], "picks": [{"s": "S00"}]})  # must not overwrite
                again = json.loads((folder / f"{cal[k][:7]}.json").read_text())
            finally:
                mom.PAPER_START = original
        self.assertEqual(saved["symbols"], ["S34", "S33"])
        self.assertEqual(again["symbols"], ["S34", "S33"])
        self.assertEqual(len(log), 1)
        self.assertTrue(log[0]["complete"])
        self.assertGreater(log[0]["portfolio"], log[0]["universe"])

    def test_not_frozen_before_paper_start(self):
        cal, data = make_data(n=320, growth={f"S{i}": 0.0001 for i in range(31)})
        panel = mom.build_panel(data, cal)
        with tempfile.TemporaryDirectory() as tmp:
            mom.paper_log(panel, cal, [1.0] * len(cal), Path(tmp), {"formed_on": "2024-01-31", "picks": []})
            self.assertEqual(list(Path(tmp).glob("*.json")), [])


if __name__ == "__main__":
    unittest.main()
