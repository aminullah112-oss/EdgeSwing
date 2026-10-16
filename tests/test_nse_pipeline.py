import csv
import io
import importlib.util
import json
import zipfile
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github" / "scripts" / "update_nse_data.py"
spec = importlib.util.spec_from_file_location("edge_nse_pipeline", SCRIPT)
pipe = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(pipe)


class PipelineTests(unittest.TestCase):
    def make_zip(self, csv_text):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("BhavCopy_NSE_CM_test.csv", csv_text)
        return buf.getvalue()

    def test_udiff_schema_filters_non_eq_series(self):
        payload = self.make_zip(
            "TradDt,TckrSymb,SctySrs,OpnPric,HghPric,LwPric,ClsPric,TtlTradgVol\n"
            "2026-10-15,ABC,EQ,100,105,99,104,150000\n"
            "2026-10-15,ABC,BE,100,106,99,105,200000\n"
        )
        rows, warnings = pipe.read_bhavcopy_zip(payload, pipe.dt.date(2026, 10, 15))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["symbol"], "ABC")
        self.assertEqual(rows[0]["close"], 104)

    def test_missing_series_is_rejected(self):
        payload = self.make_zip(
            "TradDt,TckrSymb,OpnPric,HghPric,LwPric,ClsPric,TtlTradgVol\n"
            "2026-10-15,ABC,100,105,99,104,150000\n"
        )
        with self.assertRaisesRegex(ValueError, "Series column missing"):
            pipe.read_bhavcopy_zip(payload, pipe.dt.date(2026, 10, 15))

    def test_old_bhavcopy_headers(self):
        payload = self.make_zip(
            "TIMESTAMP,SYMBOL,SERIES,OPEN,HIGH,LOW,CLOSE,TOTTRDQTY\n"
            "15-Oct-2026,ABC,EQ,100,105,99,104,150000\n"
        )
        rows, _ = pipe.read_bhavcopy_zip(payload, pipe.dt.date(2026, 10, 15))
        self.assertEqual(rows[0]["date"], "2026-10-15")
        self.assertEqual(rows[0]["volume"], 150000)

    def test_index_history_field_normalization(self):
        obj = {"data": [{"CH_TIMESTAMP": "15-Oct-2026", "CH_OPENING_PRICE": "25000",
                         "CH_TRADE_HIGH_PRICE": "25100", "CH_TRADE_LOW_PRICE": "24900",
                         "CH_CLOSING_PRICE": "25050"}]}
        row = pipe.flatten_records(obj)[0]
        normalized = {pipe.clean_header(str(k)): v for k, v in row.items()}
        self.assertEqual(pipe.iso_date(normalized["chtimestamp"]), "2026-10-15")
        self.assertEqual(pipe.num(normalized["chclosingprice"]), 25050)

    def test_nifty_daily_report_parser(self):
        raw = ("Index Name,Index Date,Open Index Value,High Index Value,Low Index Value,Closing Index Value\n"
               "NIFTY 50,15-Oct-2026,25000,25100,24900,25050\n"
               "NIFTY BANK,15-Oct-2026,56000,56500,55900,56200\n").encode()
        rows = pipe.read_nifty_daily_csv(raw, pipe.dt.date(2026, 10, 15))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["symbol"], "NIFTY50")
        self.assertEqual(rows[0]["close"], 25050)

    def test_freshness_gate_requires_recent_date_and_200_sessions(self):
        today = pipe.dt.date(2026, 10, 16)
        self.assertEqual(pipe.classify_status(199, "2026-10-16", today), "warming_up")
        self.assertEqual(pipe.classify_status(200, "2026-10-13", today), "ready")
        self.assertEqual(pipe.classify_status(200, "2026-10-12", today), "stale")
        self.assertEqual(pipe.classify_status(0, None, today), "source_unavailable")

    def test_older_symbol_bar_is_not_published_as_current_candidate(self):
        import random
        random.seed(2)
        rows = []
        close = 100.0
        day = pipe.dt.date(2025, 1, 1)
        while len(rows) < 260:
            if day.weekday() < 5:
                close *= 1 + (0.03 if len(rows) == 259 else random.gauss(0.0012, 0.009))
                opening = close * (1 + random.gauss(0, 0.002))
                volume = 2_200_000 if len(rows) == 259 else 1_000_000
                rows.append({"date": day.isoformat(), "symbol": "TEST", "open": opening,
                             "high": max(opening, close) * 1.002, "low": min(opening, close) * 0.998,
                             "close": close, "volume": volume})
            day += pipe.dt.timedelta(days=1)
        latest_test_date = rows[-1]["date"]
        newer_day = pipe.dt.date.fromisoformat(latest_test_date) + pipe.dt.timedelta(days=4)
        rows.append({"date": newer_day.isoformat(), "symbol": "OTHER", "open": 50,
                     "high": 51, "low": 49, "close": 50.5, "volume": 1000})
        candidates, meta, warnings = pipe.screen(rows)
        self.assertNotIn("TEST", [x["symbol"] for x in candidates])
        self.assertGreaterEqual(meta["stale_symbols_skipped"], 1)

    def test_rules_screen_recent_breakout_candidate(self):
        import random
        random.seed(2)
        rows = []
        close = 100.0
        day = pipe.dt.date(2025, 1, 1)
        while len(rows) < 260:
            if day.weekday() < 5:
                close *= 1 + (0.03 if len(rows) == 259 else random.gauss(0.0012, 0.009))
                opening = close * (1 + random.gauss(0, 0.002))
                volume = 2_200_000 if len(rows) == 259 else 1_000_000
                rows.append({"date": day.isoformat(), "symbol": "TEST", "open": opening,
                             "high": max(opening, close) * 1.002, "low": min(opening, close) * 0.998,
                             "close": close, "volume": volume})
            day += pipe.dt.timedelta(days=1)
        candidates, meta, warnings = pipe.screen(rows)
        self.assertEqual(meta["trading_sessions"], 260)
        self.assertGreaterEqual(meta["symbols_over_turnover_floor"], 1)
        self.assertTrue(any(x["symbol"] == "TEST" and x["strategy"] == "breakout" for x in candidates))

    def test_sma200_and_rsi_warmup(self):
        rows = []
        close = 100.0
        start = pipe.dt.date(2025, 1, 1)
        day = start
        i = 0
        while len(rows) < 225:
            if day.weekday() < 5:
                close *= 1.002 if i % 3 else 0.998
                rows.append({"date": day.isoformat(), "symbol": "ABC", "open": close * .999,
                             "high": close * 1.005, "low": close * .995, "close": close, "volume": 500000})
                i += 1
            day += pipe.dt.timedelta(days=1)
        inds = pipe.indicator_series(rows)
        self.assertTrue(inds[-1]["sma200"] > 0)
        self.assertTrue(0 <= inds[-1]["rsi"] <= 100)

    def test_close_jump_finds_corporate_action_review(self):
        rows = [{"date": "2026-10-14", "symbol": "ABC", "open": 100, "high": 102, "low": 99, "close": 100, "volume": 50000},
                {"date": "2026-10-15", "symbol": "ABC", "open": 19, "high": 20, "low": 18, "close": 20, "volume": 200000}]
        flagged = pipe.corporate_action_symbols({"ABC": rows})
        self.assertIn("ABC", flagged)
        self.assertEqual(flagged["ABC"][0]["pct"], -80.0)

    def test_official_etf_list_parser(self):
        raw = ("SYMBOL,NAME,ISIN\n"
               "LIQUIDCASE,Zerodha Nifty 1D Rate Liquid ETF,INE0XYZ\n"
               "GOLDBEES,Nippon India ETF Gold BeES,INE0ABC\n"
               "NIFTYBEES,Nippon India ETF Nifty BeES,INE0DEF\n"
               "BANKBEES,Nippon India ETF Bank BeES,INE0GHI\n"
               "ITBEES,Nippon India ETF IT BeES,INE0JKL\n"
               "JUNIORBEES,Nippon India ETF Junior BeES,INE0MNO\n"
               "SILVERBEES,Nippon India ETF Silver BeES,INE0PQR\n"
               "MON100,Motilal Oswal Nasdaq 100 ETF,INE0STU\n"
               "LIQUIDSHRI,Shri Ram Liquid ETF,INE0VWX\n"
               "CPSEETF,CPSE ETF,INE0YZA\n").encode()
        symbols = pipe.parse_etf_symbols(raw)
        self.assertIn("LIQUIDCASE", symbols)
        self.assertIn("GOLDBEES", symbols)
        self.assertEqual(len(symbols), 10)

    def test_etf_cache_fallback_includes_reported_false_candidates(self):
        self.assertIn("LIQUIDCASE", pipe.load_cached_etf_symbols())
        self.assertIn("LIQUIDSHRI", pipe.load_cached_etf_symbols())
        self.assertIn("GOLDBEES", pipe.load_cached_etf_symbols())
        self.assertIn("NIFTYBEES", pipe.load_cached_etf_symbols())

    def test_etf_rows_are_removed_before_screening(self):
        symbols = pipe.load_cached_etf_symbols()
        rows = [{"date": "2026-10-15", "symbol": s, "open": 100, "high": 101,
                 "low": 99, "close": 100.5, "volume": 100000} for s in ("LIQUIDCASE", "LIQUIDSHRI", "ABC")]
        filtered = [r for r in rows if r["symbol"] not in symbols]
        self.assertEqual([r["symbol"] for r in filtered], ["ABC"])

    def test_output_placeholder_is_valid_json(self):
        payload = json.loads((ROOT / "data" / "latest_screen.json").read_text())
        self.assertIn(payload["status"], {"awaiting_first_run", "ready", "warming_up", "stale", "source_unavailable"})
        self.assertEqual(payload["schema_version"], 2)
        self.assertIn("recent_market_dates", payload)
        self.assertIn("recent_eod_bars", payload)


    def test_recent_eod_snapshot_contains_latest_compact_bars_and_ema(self):
        rows = []
        base = pipe.dt.date(2026, 9, 1)
        for i in range(220):
            date = (base + pipe.dt.timedelta(days=i)).isoformat()
            if (base + pipe.dt.timedelta(days=i)).weekday() >= 5:
                continue
            close = 100 + i * 0.1
            rows.append({"date": date, "symbol": "ABC", "open": close, "high": close + 1,
                         "low": close - 1, "close": close, "volume": 1000})
        dates, bars = pipe.recent_eod_snapshot(rows, sessions=12)
        self.assertEqual(len(dates), 12)
        self.assertEqual(len(bars), 12)
        self.assertEqual(len(bars[0]), 8)
        self.assertEqual(bars[-1][0], dates[-1])
        self.assertEqual(bars[-1][1], "ABC")
        self.assertIsNotNone(bars[-1][6])

    def test_recent_eod_snapshot_flags_large_close_changes(self):
        rows = []
        base = pipe.dt.date(2026, 9, 1)
        for i in range(18):
            day = base + pipe.dt.timedelta(days=i)
            if day.weekday() >= 5:
                continue
            close = 100 if day < base + pipe.dt.timedelta(days=15) else 50
            rows.append({"date": day.isoformat(), "symbol": "ABC", "open": close, "high": close + 1,
                         "low": close - 1, "close": close, "volume": 1000})
        dates, bars = pipe.recent_eod_snapshot(rows, sessions=12)
        flagged = [b for b in bars if b[7] is not None and abs(b[7]) >= 40]
        self.assertTrue(flagged)


if __name__ == "__main__":
    unittest.main(verbosity=2)
