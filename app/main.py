from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal, Optional

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__, analytics
from .agents import require_key, router as agent_router
from .config import settings
from .service import Service
from .store import Store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
STATIC = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    svc = Service(Store(settings.db_path))
    svc.warm_from_cache()
    app.state.svc = svc
    tasks = [asyncio.create_task(svc.loop()), asyncio.create_task(svc.discovery_loop())]
    yield
    for t in tasks:
        t.cancel()


app = FastAPI(title="ETF Me in the ETN", version=__version__, lifespan=lifespan,
              description="Tracks every leveraged ETF, ETN and ETP (US and international), finds new "
                          "launches automatically, and screams when they crash.")
app.include_router(agent_router)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


def svc(request: Request) -> Service:
    return request.app.state.svc


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return FileResponse(STATIC / "icon.svg", media_type="image/svg+xml")


@app.get("/healthz", tags=["meta"])
def healthz(request: Request):
    s = svc(request)
    return {"ok": True, "version": __version__, "funds": len(s.universe),
            "with_data": sum(1 for b in s.board.values() if b.get("has_data")),
            "last_refresh": s.last_refresh, "refreshing": s.refreshing}


@app.get("/api/config", tags=["meta"])
def config(request: Request):
    from .llm import status
    s = svc(request)
    from .providers import alpaca_enabled
    return {"version": __version__, "demo_mode": settings.demo_mode, "providers": settings.providers,
            "alpaca": alpaca_enabled(), "discovery_sources": settings.discovery_sources,
            "refresh_minutes": settings.refresh_minutes, "tiers": s.tiers, "llm": status(),
            "agent_auth": bool(settings.agent_api_keys), "notify_channels": len(settings.notify_urls)}


@app.get("/api/funds", tags=["board"])
def funds(request: Request):
    s = svc(request)
    return {"last_refresh": s.last_refresh, "refreshing": s.refreshing, "watchlist": s.store.watchlist(),
            "funds": sorted(s.board.values(), key=lambda b: (not b.get("has_data"), b.get("drawdown") or 0))}


@app.get("/api/status", tags=["meta"], summary="Data connections: providers, keys, discovery, refresh health")
def status(request: Request):
    from . import discovery
    from .providers import status as provider_status
    s = svc(request)
    stats = {r["provider"]: r for r in s.store.provider_stats()}
    provs = [{**p, "funds": (stats.get(p["name"]) or {}).get("n", 0)} for p in provider_status()]
    keyed = {"alpaca": bool(settings.alpaca_key_id and settings.alpaca_secret), "eodhd": bool(settings.eodhd_key),
             "fmp": bool(settings.fmp_key)}
    sources = [{"name": n, "enabled": n in settings.discovery_sources, "configured": keyed.get(n, True),
                "complete": n in discovery.EXHAUSTIVE} for n in discovery.SOURCES]
    errors = sum(1 for b in s.board.values() if b.get("error"))
    return {"demo_mode": settings.demo_mode, "providers": provs, "discovery_sources": sources,
            "discovery": s.discovery_status(),
            "refresh": {"last_refresh": s.last_refresh, "refreshing": s.refreshing, "every_minutes": settings.refresh_minutes,
                        "funds": len(s.universe), "with_data": sum(1 for b in s.board.values() if b.get("has_data")),
                        "errors": errors, "never_fetched": stats.get(None, {}).get("n", 0)}}


@app.get("/api/watchlist", tags=["alerts"])
def watchlist(request: Request):
    return svc(request).store.watchlist()


@app.put("/api/watchlist/{ticker}", tags=["alerts"], summary="Star a fund (its alerts always push)")
def watch(ticker: str, request: Request, _: str = Depends(require_key)):
    s = svc(request)
    if not s.fund(ticker):
        raise HTTPException(404, "ticker not in universe")
    s.store.watch(ticker, True)
    return s.store.watchlist()


@app.delete("/api/watchlist/{ticker}", tags=["alerts"])
def unwatch(ticker: str, request: Request, _: str = Depends(require_key)):
    s = svc(request)
    s.store.watch(ticker, False)
    return s.store.watchlist()


@app.get("/api/funds/new", tags=["discovery"], summary="Funds discovery found in the last N days")
def new_funds(request: Request, days: int = 30):
    from datetime import datetime, timedelta, timezone
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    s = svc(request)
    rows = [r for r in s.store.funds() if (r["first_seen"] or "") >= cutoff]
    return sorted(rows, key=lambda r: r["first_seen"], reverse=True)


@app.get("/api/discovery", tags=["discovery"], summary="Discovery status: sources, last run, universe breakdown")
def discovery_status(request: Request):
    return svc(request).discovery_status()


@app.get("/api/discovery/funds", tags=["discovery"], summary="Every fund discovery knows about, incl. delisted")
def discovery_funds(request: Request, status: Optional[Literal["active", "delisted"]] = None):
    return svc(request).store.funds(status)


@app.post("/api/discovery/run", tags=["discovery"], summary="Scan the listing sources for leveraged funds now")
async def discovery_run(request: Request, _: str = Depends(require_key)):
    s = svc(request)
    if settings.demo_mode:
        raise HTTPException(409, "discovery is disabled in DEMO_MODE")
    if not s.discovery_running:
        asyncio.create_task(s.run_discovery())
    return {"started": True}


@app.get("/api/funds/{ticker}", tags=["board"])
def fund_detail(ticker: str, request: Request, horizon: int = 252):
    s = svc(request)
    t = ticker.upper()
    if t not in s.board:
        raise HTTPException(404, "unknown ticker")
    rows = s.store.prices(t)
    tgts = s.store.targets(t)
    return {
        "summary": s.board[t],
        "history": {"dates": [r["date"] for r in rows], "close": [r["close"] for r in rows]},
        "episodes": analytics.episodes(rows, 50),
        "scorecard": analytics.tier_scorecard(rows, s.tiers),
        "forecast": analytics.monte_carlo(rows, horizon=max(21, min(horizon, 756)), targets=tgts, seed_key=t),
        "targets": tgts,
        "agent_forecasts": s.store.forecasts(t, 20),
        "alerts": s.store.alerts(20, t),
    }


@app.get("/api/funds/{ticker}/episodes", tags=["board"])
def fund_episodes(ticker: str, request: Request, threshold: float = 50):
    return analytics.episodes(svc(request).store.prices(ticker.upper()), threshold)


@app.get("/api/alerts", tags=["alerts"])
def alerts(request: Request, limit: int = 100):
    return svc(request).store.alerts(limit)


class TargetIn(BaseModel):
    ticker: str
    price: float = Field(..., gt=0)
    direction: Literal["below", "above"] = "below"
    label: str = Field("", max_length=200)


@app.get("/api/targets", tags=["alerts"])
def targets(request: Request, ticker: Optional[str] = None):
    return svc(request).store.targets(ticker)


@app.post("/api/targets", tags=["alerts"], status_code=201)
async def add_target(body: TargetIn, request: Request, _: str = Depends(require_key)):
    s = svc(request)
    if not s.fund(body.ticker):
        raise HTTPException(404, "ticker not in universe")
    tid = s.store.add_target(body.ticker, body.price, body.direction, body.label)
    if s.board.get(body.ticker.upper(), {}).get("has_data"):
        await s.evaluate_alerts(s.board[body.ticker.upper()])
    return {"id": tid}


@app.delete("/api/targets/{tid}", tags=["alerts"])
def del_target(tid: int, request: Request, _: str = Depends(require_key)):
    svc(request).store.delete_target(tid)
    return {"ok": True}


@app.post("/api/refresh", tags=["meta"])
async def refresh(request: Request, _: str = Depends(require_key)):
    s = svc(request)
    if not s.refreshing:
        asyncio.create_task(s.refresh_all())
    return {"started": True}
