"""Discovery, classification and the Alpaca connector, against mocked HTTP."""
import asyncio
import json
from datetime import date, datetime, timedelta, timezone

import httpx
import pytest

from app import discovery, providers
from app.classify import classify
from app.config import settings
from app.providers import Bar
from app.service import Service
from app.store import Store

NASDAQ = """Symbol|Security Name|Market Category|Test Issue|Financial Status|Round Lot Size|ETF|NextShares
TQQQ|ProShares UltraPro QQQ|G|N|N|100|Y|N
SQQQ|ProShares UltraPro Short QQQ|G|N|N|100|Y|N
QQQ|Invesco QQQ Trust, Series 1|G|N|N|100|Y|N
AAPL|Apple Inc. - Common Stock|Q|N|N|100|N|N
ZZZT|Test Bull 3X|G|Y|N|100|Y|N
File Creation Time: 1122202608:00|||||||"""

OTHER = """ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|Round Lot Size|Test Issue|NASDAQ Symbol
SOXL|Direxion Shares ETF Trust - Direxion Daily Semiconductor Bull 3X Shares|P|SOXL|Y|100|N|SOXL
FNGU|MicroSectors FANG+ Index 3X Leveraged ETNs|P|FNGU|N|100|N|FNGU
NVDL|GraniteShares 2x Long NVDA Daily ETF|P|NVDL|Y|100|N|NVDL
JPST|JPMorgan Ultra-Short Income ETF|P|JPST|Y|100|N|JPST
File Creation Time: 1122202608:00|||||||"""

YAHOO = {"quotes": [
    {"symbol": "QQQ3.L", "longname": "WisdomTree NASDAQ 100 3x Daily Leveraged", "quoteType": "ETF", "exchange": "LSE"},
    {"symbol": "QQQ3.MI", "longname": "WisdomTree NASDAQ 100 3x Daily Leveraged", "quoteType": "ETF", "exchange": "MIL"},
    {"symbol": "TQQQ.MX", "longname": "ProShares UltraPro QQQ", "quoteType": "ETF", "exchange": "MEX"},
    {"symbol": "122630.KS", "longname": "KODEX Leverage", "quoteType": "ETF", "exchange": "KSC"},
    {"symbol": "1570.T", "longname": "NEXT FUNDS Nikkei 225 Leveraged Index ETF", "quoteType": "ETF", "exchange": "JPX"},
    {"symbol": "VUSA.L", "longname": "Vanguard S&P 500 UCITS ETF", "quoteType": "ETF", "exchange": "LSE"},
]}


def mock_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def listing_handler(state):
    def h(req: httpx.Request) -> httpx.Response:
        u = str(req.url)
        if u.endswith("nasdaqlisted.txt"):
            return httpx.Response(200, text=state["nasdaq"])
        if u.endswith("otherlisted.txt"):
            return httpx.Response(200, text=state["other"])
        if "finance/search" in u:
            return httpx.Response(200, json=YAHOO)
        return httpx.Response(404)
    return h


@pytest.fixture
def live(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", False)
    monkeypatch.setattr(settings, "snapshot_file", "/nonexistent/discovered.json")  # isolate from the shipped list
    monkeypatch.setattr(settings, "discovery_sources", ["nasdaqtrader", "yahoo", "alpaca"])
    monkeypatch.setattr(settings, "alpaca_key_id", "")
    monkeypatch.setattr(settings, "alpaca_secret", "")
    monkeypatch.setattr(discovery, "YAHOO_QUERIES", ["2x leveraged"])
    monkeypatch.setattr(discovery, "YAHOO_PAUSE", 0)


@pytest.mark.parametrize("name,lev,direction,typ,group", [
    ("ProShares UltraPro QQQ", 3, "long", "ETF", "broad"),
    ("ProShares UltraShort 20+ Year Treasury", 2, "short", "ETF", "rates"),
    ("Direxion Daily Semiconductor Bear 3X Shares", 3, "short", "ETF", "sector"),
    ("MicroSectors FANG+ Index 3X Leveraged ETNs", 3, "long", "ETN", "sector"),
    ("GraniteShares 2x Long NVDA Daily ETF", 2, "long", "ETF", "single-stock"),
    ("Leverage Shares -3x Short Tesla ETP Securities", 3, "short", "ETP", "single-stock"),
    ("Amundi Nasdaq-100 Daily (2x) Leveraged UCITS ETF", 2, "long", "ETF", "broad"),
    ("CSOP Hang Seng TECH Index Daily (2x) Leveraged Product", 2, "long", "ETF", "intl"),
    ("KODEX 200 Futures Inverse 2X", 2, "short", "ETF", "intl"),
    ("Volatility Shares 2x Bitcoin Strategy ETF", 2, "long", "ETF", "crypto"),
    ("ProShares Ultra VIX Short-Term Futures ETF", 2, "long", "ETF", "volatility"),
    ("AXS 1.25X NVDA Bear Daily ETF", 1.25, "short", "ETF", "single-stock"),
])
def test_classify_leveraged(name, lev, direction, typ, group):
    c = classify(name, country="KR" if "KODEX" in name else "US")
    assert c and (c.lev, c.direction, c.type, c.group) == (lev, direction, typ, group)


@pytest.mark.parametrize("name", ["Vanguard S&P 500 ETF", "JPMorgan Ultra-Short Income ETF", "Invesco Senior Loan ETF",
                                  "ProShares Short S&P500", "Direxion Daily S&P 500 Bear 1X Shares", "Invesco QQQ Trust"])
def test_classify_rejects_non_leveraged(name):
    assert classify(name) is None


def test_discover_merges_and_dedupes(live):
    async def run():
        async with mock_client(listing_handler({"nasdaq": NASDAQ, "other": OTHER})) as c:
            return await discovery.discover(c)
    res = asyncio.run(run())
    assert set(res.funds) == {"TQQQ", "SQQQ", "SOXL", "FNGU", "NVDL", "QQQ3.L", "122630.KS", "1570.T"}
    assert res.funds["SQQQ"]["direction"] == "short"
    assert res.funds["FNGU"]["type"] == "ETN"
    assert res.funds["QQQ3.L"]["currency"] == "GBP" and res.funds["QQQ3.L"]["also"] == ["QQQ3.MI"]
    assert res.funds["122630.KS"]["country"] == "KR"
    assert res.funds["TQQQ"]["sources"] == {"nasdaqtrader"}       # .MX cross-listing dropped
    assert "alpaca" in res.errors and res.errors["alpaca"].startswith("skipped")
    assert res.ok == ["nasdaqtrader", "yahoo"]


def test_inverse_can_be_excluded(live, monkeypatch):
    monkeypatch.setattr(settings, "discovery_include_inverse", False)
    funds = discovery.build([discovery.Listing("SQQQ", "ProShares UltraPro Short QQQ", "x"),
                             discovery.Listing("TQQQ", "ProShares UltraPro QQQ", "x")])
    assert list(funds) == ["TQQQ"]


def test_service_discovery_new_listing_and_delisting(live, tmp_path, monkeypatch):
    svc = Service(Store(str(tmp_path / "d.db")))

    async def no_refresh(funds):
        return None
    monkeypatch.setattr(svc, "refresh_funds", no_refresh)
    state = {"nasdaq": NASDAQ, "other": OTHER}

    async def run():
        async with mock_client(listing_handler(state)) as c:
            return await svc.run_discovery(c)

    first = asyncio.run(run())
    assert first["found"] == 8 and first["new_listings"] == []       # first scan = baseline, no alert spam
    assert not svc.fund("NVDL").new_listing                            # ... and no NEW badge either
    assert svc.fund("NVDL") and svc.fund("QQQ3.L").currency == "GBP"
    assert svc.fund("TQQQ").source == "curated"                        # curated metadata wins

    # a brand-new fund lists
    state["other"] = OTHER.replace("File Creation", "TSLT|T-Rex 2X Long Tesla Daily Target ETF|Z|TSLT|Y|100|N|TSLT\nFile Creation")
    second = asyncio.run(run())
    assert second["new_listings"] == ["TSLT"]
    assert svc.fund("TSLT").group == "single-stock" and svc.fund("TSLT").new_listing
    assert [r["ticker"] for r in svc.store.funds() if r["new_listing"]] == ["TSLT"]
    alert = svc.store.alerts(5)[0]
    assert alert["kind"] == "new_fund" and alert["ticker"] == "TSLT"

    # NVDL vanishes from the US directory; it is only delisted once it's been gone long enough
    state["other"] = state["other"].replace("NVDL|GraniteShares 2x Long NVDA Daily ETF|P|NVDL|Y|100|N|NVDL\n", "")
    assert asyncio.run(run())["delisted"] == []
    row = svc.store.get_fund("NVDL")
    row["last_seen"] = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    svc.store.upsert_fund(row)
    assert asyncio.run(run())["delisted"] == ["NVDL"]
    assert svc.fund("NVDL") is None
    # Yahoo-only funds are never delisted just for missing a search
    assert svc.store.get_fund("1570.T")["status"] == "active"
    assert svc.discovery_status()["last_run"]["found"] >= 8


def alpaca_handler(calls, sip_allowed=True):
    def h(req: httpx.Request) -> httpx.Response:
        assert req.headers["APCA-API-KEY-ID"] == "kid" and req.headers["APCA-API-SECRET-KEY"] == "sec"
        p = dict(req.url.params)
        calls.append(p)
        if p["feed"] == "sip" and not sip_allowed:
            return httpx.Response(403, json={"message": "subscription does not permit querying recent SIP data"})
        mult = 1.0 if p["adjustment"] == "split" else 0.9
        syms = p["symbols"].split(",")
        if "page_token" not in p:
            bars = {s: [{"t": "2026-11-18T05:00:00Z", "c": 10 * mult}] for s in syms}
            return httpx.Response(200, json={"bars": bars, "next_page_token": "p2"})
        bars = {s: [{"t": "2026-11-19T05:00:00Z", "c": 11 * mult}] for s in syms}
        return httpx.Response(200, json={"bars": bars, "next_page_token": None})
    return h


@pytest.fixture
def alpaca_keys(monkeypatch):
    monkeypatch.setattr(settings, "alpaca_key_id", "kid")
    monkeypatch.setattr(settings, "alpaca_secret", "sec")
    monkeypatch.setattr(settings, "alpaca_feed", "sip")


@pytest.mark.parametrize("sip_allowed", [True, False])
def test_alpaca_batch_paginates_and_falls_back_to_iex(alpaca_keys, sip_allowed):
    calls = []

    async def run():
        async with mock_client(alpaca_handler(calls, sip_allowed)) as c:
            return await providers.alpaca_batch(c, ["TQQQ", "SOXL", "QQQ3.L"], date(2026, 11, 1))
    got = asyncio.run(run())
    assert set(got) == {"TQQQ", "SOXL"}                                 # non-US symbols never sent to Alpaca
    assert [(b.date, b.close, b.adj) for b in got["TQQQ"]] == [("2026-11-18", 10, 9), ("2026-11-19", 11, 9.9)]
    assert all(c["symbols"] == "TQQQ,SOXL" for c in calls)
    assert calls[-1]["feed"] == ("sip" if sip_allowed else "iex")


def test_alpaca_assets_discovery(alpaca_keys):
    assets = [{"symbol": "TQQQ", "name": "ProShares UltraPro QQQ", "exchange": "NASDAQ", "tradable": True},
              {"symbol": "XYZQ", "name": "Some 3x Bull OTC thing", "exchange": "OTC", "tradable": True},
              {"symbol": "AAPL", "name": "Apple Inc. Common Stock", "exchange": "NASDAQ", "tradable": True}]

    async def run():
        async with mock_client(lambda r: httpx.Response(200, json=assets)) as c:
            return await discovery.alpaca(c)
    out = asyncio.run(run())
    assert [x.ticker for x in out] == ["TQQQ", "AAPL"]                 # OTC skipped; classify filters AAPL later
    assert list(discovery.build(out)) == ["TQQQ"]


def test_provider_chain_routes_by_market(alpaca_keys, monkeypatch):
    monkeypatch.setattr(settings, "providers", ["alpaca", "yahoo", "stooq", "tiingo"])
    assert providers.chain("TQQQ") == ["alpaca", "yahoo", "stooq", "tiingo"]
    assert providers.chain("TQQQ", backfill=True)[-1] == "alpaca"      # Alpaca history starts in 2016
    assert providers.chain("QQQ3.L") == ["yahoo", "stooq"]
    assert providers.chain("122630.KS") == ["yahoo"]
    assert providers.batch_provider_first("TQQQ") and not providers.batch_provider_first("QQQ3.L")


def test_reverse_split_triggers_full_refetch(live, tmp_path, monkeypatch):
    svc = Service(Store(str(tmp_path / "s.db")))
    old = [Bar(f"2026-10-{d:02d}", 10.0, 10.0) for d in range(1, 31)]
    svc.store.upsert_prices("TQQQ", old)
    seen = []

    async def fake_fetch(client, ticker, start, backfill=False, skip=()):
        seen.append((start, backfill))
        # 1:5 reverse split: the provider now reports every past close 5x higher
        return "fake", [Bar(f"2026-10-{d:02d}", 50.0, 50.0) for d in range(1, 31)] + [Bar("2026-11-02", 52.0, 52.0)]
    monkeypatch.setattr("app.service.fetch_history", fake_fetch)

    async def run():
        async with httpx.AsyncClient() as c:
            await svc.refresh_one(c, svc.fund("TQQQ"))
    asyncio.run(run())
    assert seen[-1] == (date.fromisoformat(settings.history_start), True)
    closes = {r["date"]: r["close"] for r in svc.store.prices("TQQQ")}
    assert closes["2026-10-01"] == 50.0 and closes["2026-11-02"] == 52.0


def test_discovery_api_endpoints():
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        st = c.get("/api/discovery").json()
        assert st["enabled"] is False and st["tracked"] > 70            # demo mode: curated list only
        assert c.post("/api/discovery/run").status_code == 409
        assert c.get("/api/funds/new").status_code == 200
        b = c.get("/api/agent/board?direction=long&country=US&limit=5").json()
        assert len(b) <= 5 and all(r["country"] == "US" for r in b)
        assert json.dumps(c.get("/api/config").json()["discovery_sources"])


def test_snapshot_seeds_universe_without_new_badges(live, tmp_path, monkeypatch):
    snap = tmp_path / "snap.json"
    snap.write_text(json.dumps({"generated": "2026-11-01T00:00:00+00:00", "count": 2, "funds": [
        {"ticker": "SOXS", "name": "Direxion Daily Semiconductor Bear 3X Shares", "lev": 3, "direction": "short",
         "type": "ETF", "group": "sector", "issuer": "Direxion", "under": "Semiconductor", "country": "US",
         "currency": "USD", "exchange": "US", "sources": ["nasdaqtrader"]},
        {"ticker": "QQQ3.L", "name": "WisdomTree NASDAQ 100 3x Daily Leveraged", "lev": 3, "direction": "long",
         "type": "ETP", "group": "broad", "issuer": "WisdomTree", "under": "NASDAQ 100", "country": "GB",
         "currency": "GBP", "exchange": "London", "sources": ["yahoo"]}]}))
    monkeypatch.setattr(settings, "snapshot_file", str(snap))
    svc = Service(Store(str(tmp_path / "snap.db")))
    assert svc.fund("SOXS").direction == "short" and svc.fund("QQQ3.L").currency == "GBP"
    assert not svc.fund("SOXS").new_listing
    assert svc.import_snapshot() == 0                                   # idempotent


def test_shipped_snapshot_is_valid_json_if_present():
    from pathlib import Path
    p = Path(settings.snapshot_file)
    if p.exists():
        data = json.loads(p.read_text())
        assert data["count"] == len(data["funds"]) and all("ticker" in f for f in data["funds"])
