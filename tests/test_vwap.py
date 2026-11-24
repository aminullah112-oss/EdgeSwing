import importlib.util
import io
import math
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / ".github" / "scripts"
sys.path.insert(0, str(SCRIPTS))
import update_nse_data as core  # noqa: E402

spec = importlib.util.spec_from_file_location("edge_market_vwap", SCRIPTS / "build_market_data.py")
market = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(market)


def bar(date, symbol="ABC", o=100, h=110, l=90, c=105, v=1000, value=None):
    return {"date": date, "symbol": symbol, "open": float(o), "high": float(h), "low": float(l),
            "close": float(c), "volume": float(v), "value": value}


def days(n):
    import datetime as dt
    return [(dt.date(2026, 1, 1) + dt.timedelta(days=i)).isoformat() for i in range(n)]


class ValueValidationTests(unittest.TestCase):
    def test_value_kept_when_vwap_inside_range(self):
        self.assertEqual(core.checked_value(100_000.0, bar("2026-01-01")), 100_000.0)  # vwap 100

    def test_value_rejected_when_vwap_outside_range(self):
        self.assertIsNone(core.checked_value(1_000.0, bar("2026-01-01")))        # vwap 1 (unit error)
        self.assertIsNone(core.checked_value(200_000.0, bar("2026-01-01")))      # vwap 200 > high
        self.assertIsNone(core.checked_value(None, bar("2026-01-01")))

    def test_parser_reads_udiff_traded_value(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("b.csv", "TradDt,TckrSymb,SctySrs,OpnPric,HghPric,LwPric,ClsPric,TtlTradgVol,TtlTrfVal\n"
                                 "2026-11-23,PETRONET,EQ,329,330.40,326.05,326.05,2209559,725070220.75\n"
                                 "2026-11-23,BADVAL,EQ,10,11,9,10,100,5\n")
        rows, warnings = core.read_bhavcopy_zip(buf.getvalue(), core.dt.date(2026, 11, 23))
        by = {r["symbol"]: r for r in rows}
        self.assertAlmostEqual(by["PETRONET"]["value"] / by["PETRONET"]["volume"], 328.15, places=2)
        self.assertIsNone(by["BADVAL"]["value"])
        self.assertTrue(any("traded-value" in w for w in warnings))


class IndicatorTests(unittest.TestCase):
    def test_daily_and_rolling_vwap(self):
        rows = [bar(d, c=100 + i, h=200, l=50, v=1000 + i, value=(100 + i) * (1000 + i)) for i, d in enumerate(days(25))]
        ind = core.indicator_series(rows)
        self.assertAlmostEqual(ind[-1]["vwap"], 124)
        w = range(5, 25)
        expect = sum((100 + i) * (1000 + i) for i in w) / sum(1000 + i for i in w)
        self.assertAlmostEqual(ind[-1]["vwap20"], expect)
        self.assertTrue(math.isnan(ind[18]["vwap20"]))  # fewer than 20 sessions

    def test_rolling_vwap_blank_when_any_session_lacks_value(self):
        rows = [bar(d, value=100_000.0) for d in days(25)]
        rows[-3]["value"] = None
        self.assertTrue(math.isnan(core.indicator_series(rows)[-1]["vwap20"]))

    def test_explorer_matches_pipeline(self):
        rows = [bar(d, c=100 + (i % 7), h=200, l=50, v=500 + 10 * i, value=(100 + (i % 7)) * (500 + 10 * i))
                for i, d in enumerate(days(40))]
        ref = core.indicator_series(rows)[-1]["vwap20"]
        self.assertAlmostEqual(market.series(rows)["vw20"][-1], ref)


class RefreshAndStorageTests(unittest.TestCase):
    def test_refresh_dates_are_recent_sessions_without_value(self):
        ds = days(40)
        rows = [bar(d, value=None) for d in ds[:30]] + [bar(d, value=100_000.0) for d in ds[30:]]
        need = core.vwap_refresh_dates(rows, skip={ds[29]})
        self.assertEqual(need, ds[15:29])  # last 25 sessions = ds[15:40]; ds[30:] already have value
        self.assertEqual(core.vwap_refresh_dates([bar(d, value=1e5) for d in ds], set()), [])

    def test_history_round_trip_keeps_value_and_tolerates_old_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            original = core.HISTORY_PATH, core.DATA_DIR
            core.HISTORY_PATH, core.DATA_DIR = Path(tmp) / "history.csv", Path(tmp)
            try:
                core.write_history([bar("2026-01-01", value=100_000.0), bar("2026-01-02", value=None)])
                header = core.HISTORY_PATH.read_text().splitlines()[0]
                loaded = core.load_history()
            finally:
                core.HISTORY_PATH, core.DATA_DIR = original
        self.assertTrue(header.endswith(",value"))
        self.assertEqual([r["value"] for r in loaded], [100_000.0, None])

    def test_vwap_cross_event(self):
        ds = days(30)
        rows = [bar(d, c=100, h=101, l=99, v=1000, value=100_000.0) for d in ds[:29]]
        rows[-1] = bar(ds[28], c=95, h=101, l=94, v=1000, value=97_000.0)  # dip below VWAP20
        rows.append(bar(ds[29], c=103, h=104, l=98, v=1000, value=101_000.0))  # reclaim
        self.assertIn("vw20_up", market.events_for(market.series(rows)))


if __name__ == "__main__":
    unittest.main()
