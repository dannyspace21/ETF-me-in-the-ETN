"""Runtime settings — everything comes from environment variables (12-factor),
so the same Docker image runs anywhere. See .env.example for the full list."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _bool(name: str, default: bool = False) -> bool:
    v = _env(name)
    return default if not v else v.lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    db_path: str = field(default_factory=lambda: _env("DB_PATH", str(ROOT / "data" / "etfme.db")))
    universe_file: str = field(default_factory=lambda: _env("UNIVERSE_FILE", str(ROOT / "config" / "universe.yaml")))
    alerts_file: str = field(default_factory=lambda: _env("ALERTS_FILE", str(ROOT / "config" / "alerts.yaml")))

    # Market data
    demo_mode: bool = field(default_factory=lambda: _bool("DEMO_MODE"))
    providers: list[str] = field(default_factory=lambda: [
        p.strip().lower() for p in _env("DATA_PROVIDERS", "alpaca,yahoo,stooq,eodhd").split(",") if p.strip()])
    refresh_minutes: int = field(default_factory=lambda: int(_env("REFRESH_MINUTES", "30")))
    fetch_concurrency: int = field(default_factory=lambda: int(_env("FETCH_CONCURRENCY", "6")))
    # Peak used for drawdown = highest close within the last N years (0 = true all-time high).
    # Decaying funds (commodity/single-stock) have absurd split-adjusted ATHs from a decade ago.
    peak_lookback_years: float = field(default_factory=lambda: float(_env("PEAK_LOOKBACK_YEARS", "5")))
    history_start: str = field(default_factory=lambda: _env("HISTORY_START", "2010-01-01"))
    tiingo_key: str = field(default_factory=lambda: _env("TIINGO_API_KEY"))
    polygon_key: str = field(default_factory=lambda: _env("POLYGON_API_KEY"))
    fmp_key: str = field(default_factory=lambda: _env("FMP_API_KEY"))
    alphavantage_key: str = field(default_factory=lambda: _env("ALPHAVANTAGE_API_KEY"))
    eodhd_key: str = field(default_factory=lambda: _env("EODHD_API_KEY"))
    # Alpaca (https://alpaca.markets): US asset list + batched daily bars. Free account works.
    alpaca_key_id: str = field(default_factory=lambda: _env("ALPACA_API_KEY_ID") or _env("APCA_API_KEY_ID"))
    alpaca_secret: str = field(default_factory=lambda: _env("ALPACA_API_SECRET_KEY") or _env("APCA_API_SECRET_KEY"))
    alpaca_paper: bool = field(default_factory=lambda: _bool("ALPACA_PAPER", True))
    alpaca_feed: str = field(default_factory=lambda: _env("ALPACA_DATA_FEED", "sip").lower())
    alpaca_data_url: str = field(default_factory=lambda: _env("ALPACA_DATA_URL", "https://data.alpaca.markets"))

    # Discovery: find every leveraged ETF/ETN/ETP and notice new launches
    discovery_sources: list[str] = field(default_factory=lambda: [
        s.strip().lower() for s in _env("DISCOVERY_SOURCES", "nasdaqtrader,alpaca,yahoo,eodhd,fmp").split(",")
        if s.strip()])
    discovery_hours: float = field(default_factory=lambda: float(_env("DISCOVERY_HOURS", "12")))
    discovery_include_inverse: bool = field(default_factory=lambda: _bool("DISCOVERY_INCLUDE_INVERSE", True))
    discovery_min_leverage: float = field(default_factory=lambda: float(_env("DISCOVERY_MIN_LEVERAGE", "1.25")))
    discovery_countries: list[str] = field(default_factory=lambda: [
        c.strip().upper() for c in _env("DISCOVERY_COUNTRIES").split(",") if c.strip()])
    discovery_delist_days: float = field(default_factory=lambda: float(_env("DISCOVERY_DELIST_DAYS", "7")))
    eodhd_exchanges: list[str] = field(default_factory=lambda: [
        c.strip().upper() for c in _env(
            "EODHD_EXCHANGES", "LSE,XETRA,PA,AS,MI,SW,TO,NEO,HK,KO,KQ,TW,AU,TSE,ST").split(",") if c.strip()])

    # Notifications (comma separated URLs: Discord / Slack webhooks, ntfy topics, or any JSON webhook)
    notify_urls: list[str] = field(default_factory=lambda: [
        u.strip() for u in _env("NOTIFY_URLS").split(",") if u.strip()])
    # Only push tier alerts at or above this tier key (e.g. "brown"); empty = every tier change
    notify_min_tier: str = field(default_factory=lambda: _env("NOTIFY_MIN_TIER").lower())
    notify_new_funds: bool = field(default_factory=lambda: _bool("NOTIFY_NEW_FUNDS", True))

    # Agent / AI integration
    agent_api_keys: list[str] = field(default_factory=lambda: [
        k.strip() for k in _env("AGENT_API_KEYS").split(",") if k.strip()])
    llm_provider: str = field(default_factory=lambda: _env("LLM_PROVIDER", "none").lower())
    llm_base_url: str = field(default_factory=lambda: _env("LLM_BASE_URL", "http://ollama:11434/v1"))
    llm_model: str = field(default_factory=lambda: _env("LLM_MODEL", ""))
    llm_api_key: str = field(default_factory=lambda: _env("LLM_API_KEY"))

    @property
    def alpaca_trading_url(self) -> str:
        return _env("ALPACA_TRADING_URL") or (
            "https://paper-api.alpaca.markets" if self.alpaca_paper else "https://api.alpaca.markets")

    def load_yaml(self, path: str) -> dict:
        with open(path, "r", encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}


settings = Settings()
