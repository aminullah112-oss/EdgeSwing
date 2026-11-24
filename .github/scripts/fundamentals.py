"""Fundamentals import for the market explorer.

Free automated sources are blocked from cloud runners (NSE's site returns 403 behind
bot protection; Yahoo Finance returns 429), so fundamentals come from a CSV the user
commits as ``data/fundamentals.csv``, typically a Screener.in (or similar) export
refreshed after each quarterly results season.

Only slow-moving inputs are stored: trailing EPS, shares outstanding, book value per
share and dividend per share, plus ROE/ROCE/debt-to-equity passed through as reported.
Valuation ratios are recomputed every day from the latest close:

    P/E        = close / EPS (TTM)            (None when EPS <= 0: loss-making)
    Market cap = shares x close               (Rs crore)
    P/B        = close / book value per share
    Div yield  = dividend per share / close

Accepted columns (case/punctuation-insensitive aliases; see ALIASES):
    identifier: symbol | NSE Code | ISIN | Name (matched against NSE's equity list)
    eps (TTM) | or  pe + price   -> eps = price / pe
    shares (crore) | or  market cap (Rs cr) + price -> shares = mcap / price
    book value per share, dividend per share | or dividend yield % + price
    roe %, roce %, debt to equity, as_of (YYYY-MM-DD or DD-MMM-YYYY)
When price is missing, the stored close on/before as_of is used.
"""
from __future__ import annotations

import csv
import io
import math
import re
from typing import Any, Callable

ALIASES: dict[str, tuple[str, ...]] = {
    "symbol": ("symbol", "nsecode", "nsesymbol", "ticker", "tradingsymbol", "nse"),
    "isin": ("isin", "isincode", "isinnumber"),
    "name": ("name", "companyname", "company", "nameofcompany"),
    "price": ("price", "currentprice", "cmp", "cmprs", "close", "lastprice", "currentpricers"),
    "pe": ("pe", "priceearnings", "pricetoearning", "pricetoearnings", "peratio", "stockpe"),
    "eps": ("eps", "epsttm", "eps12m", "eps12mrs", "epsrs", "epstrailing", "trailingeps"),
    "mcap_cr": ("marketcap", "marketcapitalization", "marketcapitalisation", "mcap", "marcap", "marcaprscr",
                "marketcaprscr", "marketcapcr", "mcapcr"),
    "shares_cr": ("shares", "sharescr", "sharesoutstandingcr", "noofequityshares", "noofsharescr"),
    "book_value": ("bookvalue", "bookvaluers", "bvps", "bookvaluepershare"),
    "dps": ("dps", "dividendpershare", "dividendpersharers"),
    "div_yield": ("dividendyield", "divyield", "divyld", "dividendyieldpct"),
    "roe": ("roe", "returnonequity", "roepct"),
    "roce": ("roce", "returnoncapitalemployed", "rocepct"),
    "de": ("debttoequity", "de", "debtequity", "debttoequityratio"),
    "as_of": ("asof", "date", "asofdate", "updated"),
}
NAME_NOISE = re.compile(r"\b(limited|ltd|the|co|company|corporation|corp|inc|india|plc)\b")


def norm_header(h: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (h or "").lower())


def norm_name(s: str) -> str:
    s = re.sub(r"[^a-z0-9 ]", " ", (s or "").lower().replace("&", " and "))
    return " ".join(NAME_NOISE.sub(" ", s).split())


def to_num(v: Any) -> float | None:
    if v is None:
        return None
    s = str(v).strip().replace(",", "").replace("%", "").replace("₹", "")
    if s in {"", "-", "--", "NA", "N/A", "nan", "None"}:
        return None
    try:
        x = float(s)
    except ValueError:
        return None
    return x if math.isfinite(x) else None


def parse_fundamentals_csv(content: bytes, iso_date: Callable[[Any], str | None]) -> tuple[list[dict[str, Any]], list[str]]:
    """Return canonical rows and warnings. Raises ValueError for an unusable header."""
    text = content.decode("utf-8-sig", errors="replace")
    reader = csv.reader(io.StringIO(text))
    header = next(reader, None)
    if not header:
        return [], ["data/fundamentals.csv is empty."]
    mapping: dict[str, int] = {}
    for idx, h in enumerate(header):
        key = norm_header(h)
        for field, names in ALIASES.items():
            if key in names and field not in mapping:
                mapping[field] = idx
    if not ({"symbol", "isin", "name"} & set(mapping)):
        raise ValueError(f"fundamentals CSV needs a symbol, NSE Code, ISIN or Name column; got {header}")
    if not ({"eps", "pe"} & set(mapping) or {"shares_cr", "mcap_cr"} & set(mapping)):
        raise ValueError("fundamentals CSV needs EPS or P/E, and/or shares or market cap columns")
    rows, skipped = [], 0
    for line in reader:
        if not any(c.strip() for c in line):
            continue
        get = lambda f: line[mapping[f]].strip() if f in mapping and mapping[f] < len(line) else ""
        row: dict[str, Any] = {
            "symbol": get("symbol").upper() or None,
            "isin": get("isin").upper() or None,
            "name": get("name") or None,
            "as_of": iso_date(get("as_of")) if get("as_of") else None,
        }
        for f in ("price", "pe", "eps", "mcap_cr", "shares_cr", "book_value", "dps", "div_yield", "roe", "roce", "de"):
            row[f] = to_num(get(f))
        if not (row["symbol"] or row["isin"] or row["name"]):
            skipped += 1
            continue
        rows.append(row)
    warnings = [f"Skipped {skipped} fundamentals row(s) without an identifier."] if skipped else []
    return rows, warnings


def resolve_symbols(rows: list[dict[str, Any]], known: set[str], isin_map: dict[str, str],
                    name_map: dict[str, str]) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """Map each row to an NSE symbol via symbol, then ISIN, then normalized company name."""
    out: dict[str, dict[str, Any]] = {}
    unmatched: list[str] = []
    for row in rows:
        sym = row["symbol"] if row["symbol"] in known else None
        if not sym and row["isin"]:
            sym = isin_map.get(row["isin"])
        if not sym and row["name"]:
            sym = name_map.get(norm_name(row["name"]))
        if sym and sym in known:
            out[sym] = row  # later rows win (lets users append corrections)
        else:
            unmatched.append(row["symbol"] or row["isin"] or row["name"])
    warnings = []
    if unmatched:
        preview = ", ".join(unmatched[:10])
        warnings.append(f"{len(unmatched)} fundamentals row(s) did not match an NSE EQ symbol: {preview}"
                        f"{' …' if len(unmatched) > 10 else ''}.")
    return out, warnings


def base_values(row: dict[str, Any], ref_price: float | None) -> dict[str, Any]:
    """Convert one export row into price-independent inputs (EPS, shares, BVPS, DPS)."""
    price = row["price"] if row["price"] and row["price"] > 0 else ref_price
    eps = row["eps"]
    if eps is None and row["pe"] and price:
        eps = price / row["pe"] if row["pe"] != 0 else None
    shares = row["shares_cr"]
    if shares is None and row["mcap_cr"] and price:
        shares = row["mcap_cr"] / price
    dps = row["dps"]
    if dps is None and row["div_yield"] is not None and price:
        dps = row["div_yield"] / 100 * price
    return {"eps": eps, "shares_cr": shares, "book_value": row["book_value"], "dps": dps,
            "roe": row["roe"], "roce": row["roce"], "de": row["de"], "as_of": row["as_of"]}


def daily_ratios(base: dict[str, Any], close: float) -> dict[str, float | None]:
    """Recompute valuation ratios at today's close."""
    eps, shares, bv, dps = base["eps"], base["shares_cr"], base["book_value"], base["dps"]
    return {
        "pe": close / eps if eps and eps > 0 and close > 0 else None,
        "eps": eps,
        "mcap": shares * close if shares and shares > 0 else None,
        "pb": close / bv if bv and bv > 0 else None,
        "dy": dps / close * 100 if dps is not None and dps >= 0 and close > 0 else None,
        "roe": base["roe"], "roce": base["roce"], "de": base["de"],
    }


def parse_equity_list(content: bytes) -> list[dict[str, str]]:
    """NSE EQUITY_L.csv: SYMBOL, NAME OF COMPANY, SERIES, ..., ISIN NUMBER, ..."""
    reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig", errors="replace")))
    cols = {norm_header(h): h for h in (reader.fieldnames or [])}
    if not {"symbol", "nameofcompany", "isinnumber"} <= set(cols):
        raise ValueError(f"unexpected EQUITY_L header: {reader.fieldnames}")
    out = []
    for r in reader:
        sym = (r.get(cols["symbol"]) or "").strip().upper()
        if sym:
            out.append({"symbol": sym, "name": (r.get(cols["nameofcompany"]) or "").strip(),
                        "isin": (r.get(cols["isinnumber"]) or "").strip().upper()})
    return out
