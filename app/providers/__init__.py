"""Pluggable market-data providers.

Each provider implements ``async fetch(client, ticker, start) -> list[Bar]``.
The registry is tried in the order given by ``DATA_PROVIDERS`` until one returns
data, so free keyless sources (Yahoo, Stooq) can be backed by keyed ones
(Alpaca, Tiingo, Polygon, FMP, EODHD, Alpha Vantage) — or the other way around.

Tickers use Yahoo-style suffixes for non-US listings (``QQQ3.L``, ``1570.T``);
``SUPPORTS`` says which providers can handle which market, and each provider
translates the symbol to its own dialect.

Alpaca also has a *batch* path (``alpaca_batch``): one request returns daily
bars for up to 100 symbols, which is what makes refreshing thousands of funds
practical.
"""
from __future__ import annotations

import csv
import io
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Callable
from urllib.parse import quote

import httpx

from .. import exchanges as ex
from ..config import settings

log = logging.getLogger("etfme.providers")
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) etf-me-in-the-etn/1.0"}


@dataclass
class Bar:
    date: str      # YYYY-MM-DD
    close: float   # split-adjusted close (what you'd see on a quote screen)
    adj: float     # split + dividend adjusted close (for return math)


class ProviderError(RuntimeError):
    pass


# ---------------------------------------------------------------- Yahoo (no key)
async def yahoo(client: httpx.AsyncClient, ticker: str, start: date) -> list[Bar]:
    p1 = int(datetime(start.year, start.month, start.day, tzinfo=timezone.utc).timestamp())
    p2 = int(datetime.now(timezone.utc).timestamp()) + 86400
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(ticker, safe='.')}"
    r = await client.get(url, params={"period1": p1, "period2": p2, "interval": "1d",
                                       "events": "div,split", "includeAdjustedClose": "true"}, headers=UA)
    r.raise_for_status()
    res = (r.json().get("chart") or {}).get("result") or []
    if not res:
        raise ProviderError(f"yahoo: no result for {ticker}")
    res = res[0]
    ts = res.get("timestamp") or []
    q = (res.get("indicators", {}).get("quote") or [{}])[0]
    adj = ((res.get("indicators", {}).get("adjclose") or [{}])[0]).get("adjclose") or q.get("close") or []
    closes = q.get("close") or []
    bars = []
    for i, t in enumerate(ts):
        c = closes[i] if i < len(closes) else None
        a = adj[i] if i < len(adj) else c
        if c is None:
            continue
        d = datetime.fromtimestamp(t, tz=timezone.utc).date().isoformat()
        bars.append(Bar(d, float(c), float(a if a is not None else c)))
    return bars


# ---------------------------------------------------------------- Stooq (no key)
async def stooq(client: httpx.AsyncClient, ticker: str, start: date) -> list[Bar]:
    base, suf = ex.split(ticker)
    st = ex.EXCHANGES.get(suf, ex.EXCHANGES[""]).stooq
    if not st:
        raise ProviderError(f"stooq: market .{suf} not supported")
    r = await client.get("https://stooq.com/q/d/l/", params={"s": f"{base.lower()}.{st}", "i": "d",
                                                             "d1": start.strftime("%Y%m%d")}, headers=UA)
    r.raise_for_status()
    rows = list(csv.DictReader(io.StringIO(r.text)))
    if not rows or "Close" not in rows[0]:
        raise ProviderError(f"stooq: no data for {ticker}")
    return [Bar(x["Date"], float(x["Close"]), float(x["Close"])) for x in rows if x.get("Close")]


# ---------------------------------------------------------------- Tiingo (free key)
async def tiingo(client: httpx.AsyncClient, ticker: str, start: date) -> list[Bar]:
    if not settings.tiingo_key:
        raise ProviderError("tiingo: TIINGO_API_KEY not set")
    r = await client.get(f"https://api.tiingo.com/tiingo/daily/{ticker}/prices",
                         params={"startDate": start.isoformat(), "token": settings.tiingo_key})
    r.raise_for_status()
    return [Bar(x["date"][:10], float(x["close"]), float(x.get("adjClose") or x["close"])) for x in r.json()]


# ---------------------------------------------------------------- Polygon / Massive (key)
async def polygon(client: httpx.AsyncClient, ticker: str, start: date) -> list[Bar]:
    if not settings.polygon_key:
        raise ProviderError("polygon: POLYGON_API_KEY not set")
    url = f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/1/day/{start.isoformat()}/{date.today().isoformat()}"
    r = await client.get(url, params={"adjusted": "true", "limit": 50000, "apiKey": settings.polygon_key})
    r.raise_for_status()
    out = []
    for x in r.json().get("results") or []:
        d = datetime.fromtimestamp(x["t"] / 1000, tz=timezone.utc).date().isoformat()
        out.append(Bar(d, float(x["c"]), float(x["c"])))
    return out


# ---------------------------------------------------------------- Financial Modeling Prep (key)
async def fmp(client: httpx.AsyncClient, ticker: str, start: date) -> list[Bar]:
    if not settings.fmp_key:
        raise ProviderError("fmp: FMP_API_KEY not set")
    r = await client.get("https://financialmodelingprep.com/stable/historical-price-eod/full",
                         params={"symbol": ticker, "from": start.isoformat(), "apikey": settings.fmp_key})
    r.raise_for_status()
    data = r.json()
    rows = data if isinstance(data, list) else data.get("historical", [])
    out = [Bar(x["date"][:10], float(x["close"]), float(x.get("adjClose") or x["close"])) for x in rows]
    return sorted(out, key=lambda b: b.date)


# ---------------------------------------------------------------- Alpha Vantage (key)
async def alphavantage(client: httpx.AsyncClient, ticker: str, start: date) -> list[Bar]:
    if not settings.alphavantage_key:
        raise ProviderError("alphavantage: ALPHAVANTAGE_API_KEY not set")
    r = await client.get("https://www.alphavantage.co/query", params={
        "function": "TIME_SERIES_DAILY", "symbol": ticker, "outputsize": "full", "apikey": settings.alphavantage_key})
    r.raise_for_status()
    series = r.json().get("Time Series (Daily)") or {}
    if not series:
        raise ProviderError(f"alphavantage: {r.json()}")
    out = [Bar(d, float(v["4. close"]), float(v["4. close"])) for d, v in series.items() if d >= start.isoformat()]
    return sorted(out, key=lambda b: b.date)


# ---------------------------------------------------------------- EODHD (key, global)
async def eodhd(client: httpx.AsyncClient, ticker: str, start: date) -> list[Bar]:
    if not settings.eodhd_key:
        raise ProviderError("eodhd: EODHD_API_KEY not set")
    base, suf = ex.split(ticker)
    code = ex.EXCHANGES.get(suf, ex.EXCHANGES[""]).eodhd
    r = await client.get(f"https://eodhd.com/api/eod/{base}.{code}",
                         params={"api_token": settings.eodhd_key, "fmt": "json", "from": start.isoformat()})
    r.raise_for_status()
    # EODHD's `close` is not split-adjusted; `adjusted_close` is split + dividend adjusted, so use it for both.
    return [Bar(x["date"], float(x.get("adjusted_close") or x["close"]), float(x.get("adjusted_close") or x["close"]))
            for x in r.json() if x.get("close") is not None]


# ---------------------------------------------------------------- Alpaca (key, US, batched)
ALPACA_HISTORY_START = "2016-01-01"   # Alpaca's daily history begins in 2016
ALPACA_BATCH = 100


def alpaca_enabled() -> bool:
    return bool(settings.alpaca_key_id and settings.alpaca_secret)


def _alpaca_headers() -> dict:
    return {"APCA-API-KEY-ID": settings.alpaca_key_id, "APCA-API-SECRET-KEY": settings.alpaca_secret}


async def _alpaca_bars(client: httpx.AsyncClient, symbols: list[str], start: date, adjustment: str,
                       feed: str) -> dict[str, dict[str, float]]:
    """{symbol: {date: close}} for one adjustment mode, following pagination."""
    out: dict[str, dict[str, float]] = {}
    params = {"symbols": ",".join(symbols), "timeframe": "1Day", "adjustment": adjustment, "feed": feed,
              "start": max(start.isoformat(), ALPACA_HISTORY_START), "limit": 10000, "sort": "asc"}
    if feed == "sip":  # free plans may not query the most recent 15 minutes of SIP data
        params["end"] = (datetime.now(timezone.utc) - timedelta(minutes=16)).strftime("%Y-%m-%dT%H:%M:%SZ")
    token = None
    for _ in range(500):
        if token:
            params["page_token"] = token
        r = await client.get(f"{settings.alpaca_data_url}/v2/stocks/bars", params=params, headers=_alpaca_headers())
        if r.status_code in (401, 403, 422) and feed == "sip":
            raise PermissionError(r.text[:200])
        r.raise_for_status()
        data = r.json()
        for sym, bars in (data.get("bars") or {}).items():
            d = out.setdefault(sym, {})
            for b in bars:
                d[b["t"][:10]] = float(b["c"])
        token = data.get("next_page_token")
        if not token:
            break
    return out


async def alpaca_batch(client: httpx.AsyncClient, symbols: list[str], start: date) -> dict[str, list[Bar]]:
    """Daily bars for many US symbols at once. Falls back from the SIP feed to IEX
    when the account isn't entitled to SIP."""
    if not alpaca_enabled():
        raise ProviderError("alpaca: ALPACA_API_KEY_ID / ALPACA_API_SECRET_KEY not set")
    result: dict[str, list[Bar]] = {}
    for i in range(0, len(symbols), ALPACA_BATCH):
        chunk = [s for s in symbols[i:i + ALPACA_BATCH] if ex.is_us(s)]
        if not chunk:
            continue
        feed = settings.alpaca_feed
        try:
            split_px = await _alpaca_bars(client, chunk, start, "split", feed)
        except PermissionError:
            feed = "iex"
            split_px = await _alpaca_bars(client, chunk, start, "split", feed)
        all_px = await _alpaca_bars(client, chunk, start, "all", feed)
        for sym, closes in split_px.items():
            adj = all_px.get(sym, {})
            result[sym] = [Bar(d, c, adj.get(d, c)) for d, c in sorted(closes.items())]
    return result


async def alpaca(client: httpx.AsyncClient, ticker: str, start: date) -> list[Bar]:
    return (await alpaca_batch(client, [ticker], start)).get(ticker, [])


REGISTRY: dict[str, Callable] = {"alpaca": alpaca, "yahoo": yahoo, "stooq": stooq, "tiingo": tiingo,
                                 "polygon": polygon, "fmp": fmp, "eodhd": eodhd, "alphavantage": alphavantage}
US_ONLY = {"alpaca", "tiingo", "polygon", "alphavantage"}


KEYS = {"alpaca": lambda: alpaca_enabled(), "tiingo": lambda: bool(settings.tiingo_key),
        "polygon": lambda: bool(settings.polygon_key), "fmp": lambda: bool(settings.fmp_key),
        "eodhd": lambda: bool(settings.eodhd_key), "alphavantage": lambda: bool(settings.alphavantage_key)}
MARKETS = {"alpaca": "US", "tiingo": "US", "polygon": "US", "alphavantage": "US",
           "stooq": "US, UK, DE, JP, HK", "yahoo": "US + international", "fmp": "US + international",
           "eodhd": "US + international"}


def status() -> list[dict]:
    """Every provider: is it in DATA_PROVIDERS, does it need a key, is the key set."""
    out = []
    for name in REGISTRY:
        needs = name in KEYS
        out.append({"name": name, "markets": MARKETS.get(name, ""), "needs_key": needs,
                    "configured": KEYS[name]() if needs else True, "enabled": name in settings.providers,
                    "order": settings.providers.index(name) if name in settings.providers else None})
    return sorted(out, key=lambda p: (p["order"] is None, p["order"] or 0, p["name"]))


def supports(provider: str, ticker: str) -> bool:
    if provider in US_ONLY:
        return ex.is_us(ticker)
    if provider == "stooq":
        return bool(ex.exchange_of(ticker).stooq)
    return True


def chain(ticker: str, backfill: bool = False) -> list[str]:
    """Providers to try for ``ticker``, in order. For a first-time backfill Alpaca goes last
    because its history only starts in 2016 (it's great for the daily updates after that)."""
    names = [p for p in settings.providers if supports(p, ticker)]
    if backfill and "alpaca" in names and len(names) > 1:
        names = [p for p in names if p != "alpaca"] + ["alpaca"]
    return names


def batch_provider_first(ticker: str) -> bool:
    """True when Alpaca is enabled and is the preferred provider for this (US) ticker."""
    names = chain(ticker)
    return bool(names) and names[0] == "alpaca" and alpaca_enabled()


async def fetch_history(client: httpx.AsyncClient, ticker: str, start: date,
                        backfill: bool = False, skip: tuple[str, ...] = ()) -> tuple[str, list[Bar]]:
    """Try each configured provider in order. Returns (provider_name, bars)."""
    if settings.demo_mode:
        from .demo import demo_bars
        return "demo", [b for b in demo_bars(ticker) if b.date >= start.isoformat()]
    errors = []
    for name in chain(ticker, backfill):
        if name in skip:
            continue
        fn = REGISTRY.get(name)
        if not fn:
            errors.append(f"{name}: unknown provider")
            continue
        try:
            bars = await fn(client, ticker, start)
            if bars:
                return name, bars
            errors.append(f"{name}: empty")
        except Exception as e:  # noqa: BLE001 - we want to fall through to the next provider
            errors.append(f"{name}: {type(e).__name__}: {str(e)[:120]}")
    raise ProviderError(" | ".join(errors) or f"no configured provider supports {ticker}")
