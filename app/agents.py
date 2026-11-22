"""Agent API — lets any external AI agent (LangChain, CrewAI, OpenAI Assistants,
Claude tool use, an n8n flow, a cron'd Python script…) read the board and post
forecasts back. Discoverable via /api/agent/manifest and the OpenAPI spec at /docs."""
from __future__ import annotations

from typing import Literal, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field

from . import analytics, llm
from .config import settings

router = APIRouter(prefix="/api/agent", tags=["agents"])


def require_key(authorization: str | None = Header(None), x_api_key: str | None = Header(None)) -> str:
    """If AGENT_API_KEYS is set, writes/LLM calls need `X-API-Key: <key>` or `Authorization: Bearer <key>`."""
    if not settings.agent_api_keys:
        return "anonymous"
    key = x_api_key or (authorization or "").removeprefix("Bearer ").strip()
    if key not in settings.agent_api_keys:
        raise HTTPException(401, "invalid or missing agent API key")
    return key


class ForecastIn(BaseModel):
    agent: str = Field(..., max_length=80, description="Name of your agent / model, e.g. 'claude-analyst'")
    ticker: str = Field(..., max_length=10)
    horizon_days: int = Field(252, ge=1, le=2520, description="Forecast horizon in trading days")
    target_price: Optional[float] = Field(None, gt=0, description="Expected price at the horizon")
    probability: Optional[float] = Field(None, ge=0, le=100, description="Confidence %, 0-100")
    stance: Literal["accumulate", "hold", "wait", "avoid"] = "hold"
    rationale: str = Field("", max_length=4000)


class AskIn(BaseModel):
    ticker: str
    question: str = Field("Is this a good time to start scaling in?", max_length=2000)


def context_for(svc, ticker: str) -> dict:
    s = svc.board.get(ticker.upper())
    if not s or not s.get("has_data"):
        raise HTTPException(404, f"no data for {ticker}")
    rows = svc.store.prices(ticker.upper())
    tgts = svc.store.targets(ticker)
    mc = analytics.monte_carlo(rows, targets=tgts, seed_key=ticker)
    mc.pop("fan", None), mc.pop("fan_days", None)
    return {
        "summary": {k: v for k, v in s.items() if k != "spark"},
        "tier": svc.tier(s["tier"]),
        "alert_ladder": [{**t, "price": round(s["ath"] * (1 - t["min_dd"] / 100), 2)} for t in svc.tiers],
        "crash_episodes_50pct": analytics.episodes(rows, 50),
        "tier_scorecard": analytics.tier_scorecard(rows, svc.tiers),
        "monte_carlo_1y": mc,
        "user_targets": tgts,
        "recent_agent_forecasts": svc.store.forecasts(ticker, 5),
    }


@router.get("/manifest", summary="Tool manifest for LLM agents (OpenAI/Anthropic function-calling style)")
def manifest(request: Request):
    base = str(request.base_url).rstrip("/")
    return {
        "name": "etf-me-in-the-etn", "description": "Crash tracker for every leveraged ETF/ETN/ETP (US + international, bull and bear).",
        "auth": "X-API-Key header (only required when the server sets AGENT_API_KEYS) for POST endpoints",
        "openapi": f"{base}/openapi.json",
        "tools": [
            {"name": "get_board", "method": "GET", "url": f"{base}/api/agent/board",
             "description": "All tracked funds sorted by drawdown, with alert tier. Filter by drawdown, "
                            "direction (long/short), country (ISO code, e.g. US, GB, KR), group, minimum leverage.",
             "input_schema": {"type": "object", "properties": {
                 "min_drawdown": {"type": "number"}, "direction": {"type": "string", "enum": ["long", "short"]},
                 "country": {"type": "string"}, "group": {"type": "string"}, "min_lev": {"type": "number"},
                 "limit": {"type": "integer"}}}},
            {"name": "get_context", "method": "GET", "url": f"{base}/api/agent/context/{{ticker}}",
             "description": "Everything about one fund: summary, crash history, tier scorecard, Monte Carlo.",
             "input_schema": {"type": "object", "properties": {"ticker": {"type": "string"}}, "required": ["ticker"]}},
            {"name": "post_forecast", "method": "POST", "url": f"{base}/api/agent/forecasts",
             "description": "Publish your forecast so it shows up in the UI.",
             "input_schema": ForecastIn.model_json_schema()},
            {"name": "ask_local_llm", "method": "POST", "url": f"{base}/api/agent/ask",
             "description": "Ask the server's configured LLM about a fund.",
             "input_schema": AskIn.model_json_schema()},
        ],
    }


@router.get("/board", summary="Compact board for agents")
def board(request: Request, min_drawdown: float = 0, direction: Optional[Literal["long", "short"]] = None,
          country: Optional[str] = None, group: Optional[str] = None, min_lev: float = 0, limit: int = 500):
    svc = request.app.state.svc
    rows = [{k: s.get(k) for k in ("ticker", "lev", "direction", "type", "under", "country", "currency", "price",
                                   "ath", "drawdown", "tier", "total_return", "since_label", "change_1d", "as_of")}
            for s in svc.board.values() if s.get("has_data") and -(s["drawdown"] or 0) >= min_drawdown
            and (not direction or s.get("direction") == direction)
            and (not country or s.get("country") == country.upper())
            and (not group or s.get("group") == group) and (s.get("lev") or 0) >= min_lev]
    return sorted(rows, key=lambda r: r["drawdown"])[:max(1, limit)]


@router.get("/context/{ticker}", summary="Full analysis context for one fund")
def context(ticker: str, request: Request):
    return context_for(request.app.state.svc, ticker)


@router.get("/forecasts", summary="List agent forecasts")
def list_forecasts(request: Request, ticker: Optional[str] = None, limit: int = 50):
    return request.app.state.svc.store.forecasts(ticker, limit)


@router.post("/forecasts", summary="Submit an agent forecast", status_code=201)
def post_forecast(body: ForecastIn, request: Request, _: str = Depends(require_key)):
    svc = request.app.state.svc
    if not svc.fund(body.ticker):
        raise HTTPException(404, "ticker not in universe")
    fid = svc.store.add_forecast(**body.model_dump())
    return {"id": fid, "ok": True}


@router.get("/llm", summary="Server-side LLM configuration")
def llm_status():
    return llm.status()


@router.post("/ask", summary="Ask the configured LLM (local Ollama by default) about a fund")
async def ask(body: AskIn, request: Request, _: str = Depends(require_key)):
    svc = request.app.state.svc
    ctx = context_for(svc, body.ticker)
    try:
        res = await llm.ask(body.question, ctx)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"LLM error: {e}") from e
    svc.store.add_forecast(agent=f"{res['provider']}:{res['model']}", ticker=body.ticker, horizon_days=None,
                           target_price=None, probability=None, stance="note",
                           rationale=f"Q: {body.question}\n\n{res['answer']}")
    return res
