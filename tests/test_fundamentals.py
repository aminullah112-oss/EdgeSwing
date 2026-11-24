import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / ".github" / "scripts"
sys.path.insert(0, str(SCRIPTS))
import fundamentals as fund  # noqa: E402

spec = importlib.util.spec_from_file_location("edge_market_data_f", SCRIPTS / "build_market_data.py")
market = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(market)
iso = market.core.iso_date


class ParseTests(unittest.TestCase):
    SCREENER = ("Name,NSE Code,Current Price,Price to Earning,Market Capitalization,Book value,Dividend yield,"
                "Return on equity,Return on capital employed,Debt to equity\n"
                "Reliance Industries,RELIANCE,1400,25,1894000,650,0.4,9.5,10.1,0.44\n"
                "Loss Maker Ltd,LOSS,50,-12,500,20,0,-5,-2,1.8\n").encode()

    def test_screener_headers_are_recognised(self):
        rows, warnings = fund.parse_fundamentals_csv(self.SCREENER, iso)
        self.assertEqual(warnings, [])
        r = rows[0]
        self.assertEqual((r["symbol"], r["price"], r["pe"], r["mcap_cr"], r["roe"]), ("RELIANCE", 1400, 25, 1894000, 9.5))

    def test_template_with_eps_and_shares(self):
        csv = b"symbol,as_of,eps_ttm,shares_cr,book_value,dps\nTCS,2026-10-15,130,361.8,280,120\n"
        rows, _ = fund.parse_fundamentals_csv(csv, iso)
        self.assertEqual(rows[0]["eps"], 130)
        self.assertEqual(rows[0]["as_of"], "2026-10-15")

    def test_numbers_with_commas_and_percent(self):
        csv = b'symbol,eps,market cap,roe\nABC,"1,234.5","12,00,000",18.2%\n'
        r = fund.parse_fundamentals_csv(csv, iso)[0][0]
        self.assertEqual((r["eps"], r["mcap_cr"], r["roe"]), (1234.5, 1200000, 18.2))

    def test_missing_identifier_column_rejected(self):
        with self.assertRaises(ValueError):
            fund.parse_fundamentals_csv(b"pe,eps\n10,2\n", iso)

    def test_missing_value_columns_rejected(self):
        with self.assertRaises(ValueError):
            fund.parse_fundamentals_csv(b"symbol,roe\nABC,10\n", iso)


class DeriveTests(unittest.TestCase):
    def row(self, **kw):
        base = {k: None for k in ("symbol", "isin", "name", "as_of", "price", "pe", "eps", "mcap_cr", "shares_cr",
                                  "book_value", "dps", "div_yield", "roe", "roce", "de")}
        return {**base, **kw}

    def test_ratios_recompute_at_new_close(self):
        base = fund.base_values(self.row(price=1400, pe=25, mcap_cr=1894000, book_value=650, div_yield=0.4), None)
        self.assertAlmostEqual(base["eps"], 56.0)
        self.assertAlmostEqual(base["shares_cr"], 1352.857, places=2)
        r = fund.daily_ratios(base, 1351.0)                   # price fell after the export
        self.assertAlmostEqual(r["pe"], 1351 / 56, places=6)  # P/E falls with price
        self.assertAlmostEqual(r["mcap"], 1894000 * 1351 / 1400, places=0)
        self.assertAlmostEqual(r["pb"], 1351 / 650, places=6)
        self.assertAlmostEqual(r["dy"], 5.6 / 1351 * 100, places=6)

    def test_loss_maker_has_no_pe(self):
        base = fund.base_values(self.row(price=50, pe=-12, mcap_cr=500), None)
        self.assertLess(base["eps"], 0)
        self.assertIsNone(fund.daily_ratios(base, 50)["pe"])
        self.assertIsNotNone(fund.daily_ratios(base, 50)["mcap"])

    def test_reference_price_used_when_export_has_no_price(self):
        base = fund.base_values(self.row(pe=20, mcap_cr=1000), ref_price=100)
        self.assertEqual((base["eps"], base["shares_cr"]), (5, 10))


class ResolveTests(unittest.TestCase):
    def test_symbol_isin_and_name_matching(self):
        rows = [
            {"symbol": "TCS", "isin": None, "name": None},
            {"symbol": None, "isin": "INE002A01018", "name": None},
            {"symbol": None, "isin": None, "name": "Mahindra & Mahindra Limited"},
            {"symbol": "NOPE", "isin": None, "name": None},
        ]
        isin_map = {"INE002A01018": "RELIANCE"}
        name_map = {fund.norm_name("Mahindra & Mahindra Ltd."): "M&M"}
        out, warnings = fund.resolve_symbols(rows, {"TCS", "RELIANCE", "M&M"}, isin_map, name_map)
        self.assertEqual(set(out), {"TCS", "RELIANCE", "M&M"})
        self.assertIn("NOPE", warnings[0])

    def test_equity_list_parser(self):
        csv = (b"SYMBOL,NAME OF COMPANY, SERIES, DATE OF LISTING, PAID UP VALUE, MARKET LOT, ISIN NUMBER, FACE VALUE\n"
               b"20MICRONS,20 Microns Limited,EQ,06-OCT-2008,5,1,INE144J01027,5\n")
        self.assertEqual(fund.parse_equity_list(csv),
                         [{"symbol": "20MICRONS", "name": "20 Microns Limited", "isin": "INE144J01027"}])


class BuildIntegrationTests(unittest.TestCase):
    def bars(self, symbol, closes):
        import datetime as dt
        d0 = dt.date(2026, 1, 1)
        return [{"date": (d0 + dt.timedelta(days=i)).isoformat(), "symbol": symbol, "open": c, "high": c * 1.01,
                 "low": c * 0.99, "close": c, "volume": 1000.0} for i, c in enumerate(closes)]

    def test_snapshot_carries_daily_fundamentals_and_blanks_after_split(self):
        good = self.bars("GOOD", [100.0] * 30 + [120.0] * 10)
        split = self.bars("SPLIT", [200.0] * 30 + [100.0] * 10)  # halves on day 30 (2026-01-31)
        export = {
            "GOOD": {"symbol": "GOOD", "isin": None, "name": None, "as_of": "2026-01-15", "price": None, "pe": 10,
                     "eps": None, "mcap_cr": 1000, "shares_cr": None, "book_value": 50, "dps": None, "div_yield": None,
                     "roe": 20, "roce": 25, "de": 0.1},
            "SPLIT": {"symbol": "SPLIT", "isin": None, "name": None, "as_of": "2026-01-15", "price": None, "pe": 10,
                      "eps": None, "mcap_cr": 1000, "shares_cr": None, "book_value": None, "dps": None,
                      "div_yield": None, "roe": None, "roce": None, "de": None},
        }
        with tempfile.TemporaryDirectory() as tmp:
            warnings = []
            market.build(good + split, {}, Path(tmp) / "m", "test", warnings, [], export, None)
            snap = json.loads((Path(tmp) / "m" / "snapshot.json").read_text())
        rows = {r[0]: dict(zip(snap["columns"], r)) for r in snap["rows"]}
        g = rows["GOOD"]
        self.assertAlmostEqual(g["pe"], 12.0)    # eps 10 at the as_of close of 100, now 120
        self.assertEqual(g["mcap"], 1200)
        self.assertEqual(g["fa"], "2026-01-15")
        self.assertIsNone(rows["SPLIT"]["pe"])
        self.assertIsNone(rows["SPLIT"]["mcap"])
        self.assertTrue(any("SPLIT" in w for w in snap["meta"]["warnings"]))
        self.assertEqual(snap["meta"]["fundamentals"]["stocks"], 1)


if __name__ == "__main__":
    unittest.main()
