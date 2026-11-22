"""Pluggable market-data providers.

Each provider implements ``async fetch(client, ticker, start) -> list[Bar]``.
The registry is tried in the order given by ``DATA_PROVIDERS`` until one returns
data, so free keyless sources (Yahoo, Stooq) can be backed by keyed ones
(Tiingo, Polygon, FMP, Alpha Vantage) — or the other way around.
"""
from __future__ import annotations

import csv
import io
import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone

import httpx

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
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
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
    r = await client.get("https://stooq.com/q/d/l/", params={"s": f"{ticker.lower()}.us", "i": "d",
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


REGISTRY = {"yahoo": yahoo, "stooq": stooq, "tiingo": tiingo, "polygon": polygon,
            "fmp": fmp, "alphavantage": alphavantage}


async def fetch_history(client: httpx.AsyncClient, ticker: str, start: date) -> tuple[str, list[Bar]]:
    """Try each configured provider in order. Returns (provider_name, bars)."""
    if settings.demo_mode:
        from .demo import demo_bars
        return "demo", [b for b in demo_bars(ticker) if b.date >= start.isoformat()]
    errors = []
    for name in settings.providers:
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
    raise ProviderError(" | ".join(errors))
