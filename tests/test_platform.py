"""HTTP retry layer, watchlist and the /api/status connections endpoint."""
import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient

from app import net
from app.main import app


def run_with(handler, **kw):
    calls = []

    def h(req):
        calls.append(req)
        return handler(len(calls), req)

    async def go():
        t = net.RetryTransport(httpx.MockTransport(h), backoff=0.001, **kw)
        async with httpx.AsyncClient(transport=t) as c:
            return await c.get("https://query1.finance.yahoo.com/x")
    return asyncio.run(go()), calls


def test_retries_429_then_succeeds():
    r, calls = run_with(lambda n, _: httpx.Response(429, headers={"Retry-After": "0"}) if n < 3 else httpx.Response(200, text="ok"))
    assert r.status_code == 200 and len(calls) == 3


def test_retries_connection_errors():
    def h(n, req):
        if n == 1:
            raise httpx.ConnectError("boom", request=req)
        return httpx.Response(200)
    r, calls = run_with(h)
    assert r.status_code == 200 and len(calls) == 2


def test_gives_up_and_returns_last_error_status():
    r, calls = run_with(lambda n, _: httpx.Response(503), retries=2)
    assert r.status_code == 503 and len(calls) == 3


def test_does_not_retry_client_errors():
    r, calls = run_with(lambda n, _: httpx.Response(403))   # e.g. Alpaca SIP not in plan: must fall back, not retry
    assert r.status_code == 403 and len(calls) == 1


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def test_watchlist_roundtrip_and_push_rule(client):
    assert client.put("/api/watchlist/SOXL").json() == ["SOXL"]
    assert client.put("/api/watchlist/NOPE").status_code == 404
    assert client.get("/api/funds").json()["watchlist"] == ["SOXL"]
    svc = app.state.svc
    from app.config import settings
    old = settings.notify_min_tier
    settings.notify_min_tier = "allin"
    try:
        tier_alert = {"kind": "tier", "severity": "clench"}
        assert svc.should_push({**tier_alert, "ticker": "SOXL"})        # starred: always
        assert not svc.should_push({**tier_alert, "ticker": "TQQQ"})    # below NOTIFY_MIN_TIER
    finally:
        settings.notify_min_tier = old
    assert client.delete("/api/watchlist/SOXL").json() == []


def test_status_reports_connections(client):
    st = client.get("/api/status").json()
    names = [p["name"] for p in st["providers"]]
    assert names[:2] == ["alpaca", "yahoo"]                             # DATA_PROVIDERS order
    alpaca = st["providers"][0]
    assert alpaca["needs_key"] and not alpaca["configured"]
    assert {s["name"] for s in st["discovery_sources"]} >= {"nasdaqtrader", "alpaca", "yahoo", "eodhd", "fmp"}
    assert st["refresh"]["funds"] > 70 and st["demo_mode"] is True
