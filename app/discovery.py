"""Finds *every* listed leveraged ETF / ETN / ETP, in the US and abroad, and
notices new launches.

Each source returns raw listings (symbol + name). They are run through
``classify`` to keep only leveraged products, then merged and de-duplicated.

Source          Key?   Coverage
--------------  -----  -----------------------------------------------------------
nasdaqtrader    no     every US-listed security (Nasdaq, NYSE, NYSE Arca, Cboe BZX)
alpaca          yes    every US security tradable on Alpaca
yahoo           no     global, keyword search (London, Xetra, Paris, Toronto, HK,
                       Tokyo, Korea, Taiwan, ASX …); best effort, not exhaustive
eodhd           yes    full symbol lists of every exchange in EODHD_EXCHANGES
fmp             yes    FMP's global ETF list

"Exhaustive" sources list a whole market, so a fund that disappears from all of
them for DISCOVERY_DELIST_DAYS is marked delisted. Yahoo search is a search, so
missing from it proves nothing.
"""
from __future__ import annotations

import asyncio
import csv
import io
import logging
import re
from dataclasses import dataclass, field

import httpx

from . import exchanges as ex
from .classify import classify
from .config import settings

log = logging.getLogger("etfme.discovery")
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) etf-me-in-the-etn/2.0"}
EXHAUSTIVE = {"nasdaqtrader", "alpaca", "eodhd", "fmp"}
YAHOO_PAUSE = 0.25


@dataclass
class Listing:
    ticker: str          # canonical (Yahoo-style) symbol
    name: str
    source: str
    hint_type: str | None = None
    isin: str | None = None


@dataclass
class Result:
    funds: dict[str, dict] = field(default_factory=dict)
    ok: list[str] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)
    raw_counts: dict[str, int] = field(default_factory=dict)


class SourceSkipped(RuntimeError):
    """Source not configured (e.g. no API key). Not an error."""


_SYM_OK = re.compile(r"^[A-Z0-9]{1,6}$")


# ---------------------------------------------------------------- Nasdaq Trader (no key, US)
async def nasdaqtrader(client: httpx.AsyncClient) -> list[Listing]:
    out: list[Listing] = []
    base = "https://www.nasdaqtrader.com/dynamic/SymDir/"
    for fname, sym_col in (("nasdaqlisted.txt", "Symbol"), ("otherlisted.txt", "ACT Symbol")):
        r = await client.get(base + fname, headers=UA)
        r.raise_for_status()
        lines = [ln for ln in r.text.splitlines() if ln and not ln.startswith("File Creation Time")]
        for row in csv.DictReader(lines, delimiter="|"):
            sym, name = (row.get(sym_col) or "").strip(), (row.get("Security Name") or "").strip()
            if row.get("Test Issue") == "Y" or not _SYM_OK.match(sym):
                continue
            is_etf = row.get("ETF") == "Y"
            if not is_etf and not re.search(r"\bETNs?\b|Exchange Traded Note|\bNotes?\b", name, re.I):
                continue
            out.append(Listing(sym, name, "nasdaqtrader", "ETF" if is_etf else "ETN"))
    if not out:
        raise RuntimeError("nasdaqtrader: empty symbol directory")
    return out


# ---------------------------------------------------------------- Alpaca (key, US)
def alpaca_headers() -> dict:
    if not (settings.alpaca_key_id and settings.alpaca_secret):
        raise SourceSkipped("alpaca: ALPACA_API_KEY_ID / ALPACA_API_SECRET_KEY not set")
    return {"APCA-API-KEY-ID": settings.alpaca_key_id, "APCA-API-SECRET-KEY": settings.alpaca_secret}


async def alpaca(client: httpx.AsyncClient) -> list[Listing]:
    r = await client.get(f"{settings.alpaca_trading_url}/v2/assets", headers=alpaca_headers(),
                         params={"status": "active", "asset_class": "us_equity"})
    r.raise_for_status()
    out = []
    for a in r.json():
        sym = a.get("symbol", "")
        if a.get("exchange") == "OTC" or not a.get("tradable", True) or not _SYM_OK.match(sym):
            continue
        out.append(Listing(sym, a.get("name") or "", "alpaca"))
    return out


# ---------------------------------------------------------------- Yahoo search (no key, global)
YAHOO_QUERIES = [
    # US / generic
    "2x leveraged", "3x leveraged", "2x long daily", "3x long daily", "2x short daily", "3x short daily",
    "daily bull 2x", "daily bull 3x", "daily bear 3x", "ultrapro", "proshares ultra", "1.5x daily", "1.25x daily",
    "daily target 2x", "leveraged etn", "4x daily", "5x long",
    # Europe
    "leverage shares 3x", "leverage shares 2x", "leverage shares 5x", "wisdomtree 3x daily", "wisdomtree 2x daily",
    "graniteshares 3x", "(2x) leveraged ucits", "daily leveraged ucits", "levdax", "x2 leveraged", "21shares 2x",
    # Canada / Australia
    "betapro 2x daily bull", "global x 2x daily", "geared fund", "ultra long",
    # Asia
    "daily (2x) leveraged product", "csop 2x leveraged", "samsung 2x leveraged", "nikkei leveraged index",
    "topix leveraged", "kodex leverage", "tiger leverage", "kbstar leverage", "rise leverage", "taiwan 50 bull 2x",
    "daily bull 2x etf yuanta", "double inverse",
]


async def yahoo(client: httpx.AsyncClient) -> list[Listing]:
    out: list[Listing] = []
    failures = 0
    for q in YAHOO_QUERIES:
        try:
            r = await client.get("https://query2.finance.yahoo.com/v1/finance/search", headers=UA, params={
                "q": q, "quotesCount": 100, "newsCount": 0, "listsCount": 0, "enableFuzzyQuery": "false"})
            r.raise_for_status()
        except Exception as e:  # noqa: BLE001
            failures += 1
            log.debug("yahoo search %r failed: %s", q, e)
            continue
        for qt in r.json().get("quotes") or []:
            if qt.get("quoteType") not in ("ETF", "EQUITY", "MUTUALFUND"):
                continue
            sym = qt.get("symbol") or ""
            suf = ex.YAHOO_EXCH_TO_SUFFIX.get(qt.get("exchange", ""), ex.split(sym)[1])
            if suf is None or ex.split(sym)[1] in ex.CROSS_LISTING_SUFFIXES:
                continue
            out.append(Listing(sym.upper(), qt.get("longname") or qt.get("shortname") or "", "yahoo",
                               "ETF" if qt.get("quoteType") == "ETF" else None))
        await asyncio.sleep(YAHOO_PAUSE)  # be polite
    if failures == len(YAHOO_QUERIES):
        raise RuntimeError("yahoo: every search failed")
    return out


# ---------------------------------------------------------------- EODHD (key, global, exhaustive)
async def eodhd(client: httpx.AsyncClient) -> list[Listing]:
    if not settings.eodhd_key:
        raise SourceSkipped("eodhd: EODHD_API_KEY not set")
    out: list[Listing] = []
    errors = []
    for code in settings.eodhd_exchanges:
        suf = ex.EODHD_TO_SUFFIX.get(code)
        if suf is None:
            errors.append(f"{code}: unknown exchange")
            continue
        try:
            r = await client.get(f"https://eodhd.com/api/exchange-symbol-list/{code}",
                                 params={"api_token": settings.eodhd_key, "fmt": "json"})
            r.raise_for_status()
            rows = r.json()
        except Exception as e:  # noqa: BLE001
            errors.append(f"{code}: {type(e).__name__}")
            continue
        for row in rows:
            typ, name, sym = str(row.get("Type") or ""), str(row.get("Name") or ""), str(row.get("Code") or "")
            if "stock" in typ.lower() and not re.search(r"\b(ETF|ETN|ETP|ETC)s?\b", name):
                continue
            if code in ("HK",) and sym.isdigit():
                sym = sym.zfill(4)
            out.append(Listing(f"{sym}.{suf}".upper() if suf else sym.upper(), name, "eodhd",
                               "ETF" if typ.upper() == "ETF" else None, row.get("Isin")))
    if errors:
        # partial coverage: keep what we got, but don't let it delist anything
        raise PartialResult(out, "eodhd: " + "; ".join(errors))
    return out


class PartialResult(RuntimeError):
    def __init__(self, listings: list[Listing], msg: str):
        super().__init__(msg)
        self.listings = listings


# ---------------------------------------------------------------- Financial Modeling Prep (key, global)
async def fmp(client: httpx.AsyncClient) -> list[Listing]:
    if not settings.fmp_key:
        raise SourceSkipped("fmp: FMP_API_KEY not set")
    r = await client.get("https://financialmodelingprep.com/stable/etf-list", params={"apikey": settings.fmp_key})
    r.raise_for_status()
    return [Listing(str(x["symbol"]).upper(), x.get("name") or "", "fmp", "ETF") for x in r.json() if x.get("symbol")]


SOURCES = {"nasdaqtrader": nasdaqtrader, "alpaca": alpaca, "yahoo": yahoo, "eodhd": eodhd, "fmp": fmp}


# ---------------------------------------------------------------- merge
def _norm_name(n: str) -> str:
    n = re.sub(r"\b(acc|dist|usd|eur|gbp|chf|ucits|etp|etf|securities|plc|ltd)\b", "", n.lower())
    return re.sub(r"[^a-z0-9]", "", n)


def build(listings: list[Listing]) -> dict[str, dict]:
    """Classify + de-duplicate. Returns {ticker: fund dict}."""
    by_ticker: dict[str, dict] = {}
    for li in listings:
        base, suf = ex.split(li.ticker)
        if suf in ex.CROSS_LISTING_SUFFIXES and suf not in ex.EXCHANGES:
            continue
        exch = ex.exchange_of(li.ticker)
        if settings.discovery_countries and exch.country not in settings.discovery_countries:
            continue
        c = classify(li.name, symbol=base, hint_type=li.hint_type, country=exch.country)
        if not c or c.lev < settings.discovery_min_leverage:
            continue
        if c.direction == "short" and not settings.discovery_include_inverse:
            continue
        cur = by_ticker.get(li.ticker)
        if cur:
            cur["sources"].add(li.source)
            cur["isin"] = cur["isin"] or li.isin
            if len(li.name) > len(cur["name"]) and li.source != "yahoo":
                cur["name"] = li.name
            continue
        by_ticker[li.ticker] = {
            "ticker": li.ticker, "name": li.name, "lev": c.lev, "direction": c.direction, "type": c.type,
            "group": c.group, "issuer": c.issuer, "under": c.under, "country": exch.country,
            "currency": exch.currency, "exchange": exch.name, "sources": {li.source}, "isin": li.isin}
    # The same European ETP trades on several venues under different tickers: keep one.
    keep: dict[str, dict] = {}
    for f in sorted(by_ticker.values(), key=lambda f: ex.venue_rank(f["ticker"])):
        if f["country"] == "US":
            keep[f["ticker"]] = f
            continue
        key = f["isin"] or f"{_norm_name(f['name'])}|{f['lev']}|{f['direction']}"
        if key in keep:
            keep[key]["sources"] |= f["sources"]
            keep[key].setdefault("also", []).append(f["ticker"])
        else:
            keep[key] = f
    return {f["ticker"]: f for f in keep.values()}


async def discover(client: httpx.AsyncClient, sources: list[str] | None = None) -> Result:
    res = Result()
    listings: list[Listing] = []
    for name in sources if sources is not None else settings.discovery_sources:
        fn = SOURCES.get(name)
        if not fn:
            res.errors[name] = "unknown source"
            continue
        try:
            got = await fn(client)
            res.ok.append(name)
        except SourceSkipped as e:
            res.errors[name] = f"skipped: {e}"
            continue
        except PartialResult as e:
            got = e.listings
            res.errors[name] = str(e)
        except Exception as e:  # noqa: BLE001
            res.errors[name] = f"{type(e).__name__}: {str(e)[:200]}"
            log.warning("discovery source %s failed: %s", name, e)
            continue
        res.raw_counts[name] = len(got)
        listings.extend(got)
    res.funds = build(listings)
    log.info("discovery: %d leveraged funds from %s (errors: %s)", len(res.funds), res.ok, res.errors or "none")
    return res
