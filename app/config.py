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
        p.strip() for p in _env("DATA_PROVIDERS", "yahoo,stooq").split(",") if p.strip()])
    refresh_minutes: int = field(default_factory=lambda: int(_env("REFRESH_MINUTES", "30")))
    # Peak used for drawdown = highest close within the last N years (0 = true all-time high).
    # Decaying funds (commodity/single-stock) have absurd split-adjusted ATHs from a decade ago.
    peak_lookback_years: float = field(default_factory=lambda: float(_env("PEAK_LOOKBACK_YEARS", "5")))
    history_start: str = field(default_factory=lambda: _env("HISTORY_START", "2010-01-01"))
    tiingo_key: str = field(default_factory=lambda: _env("TIINGO_API_KEY"))
    polygon_key: str = field(default_factory=lambda: _env("POLYGON_API_KEY"))
    fmp_key: str = field(default_factory=lambda: _env("FMP_API_KEY"))
    alphavantage_key: str = field(default_factory=lambda: _env("ALPHAVANTAGE_API_KEY"))

    # Notifications (comma separated URLs: Discord / Slack webhooks, ntfy topics, or any JSON webhook)
    notify_urls: list[str] = field(default_factory=lambda: [
        u.strip() for u in _env("NOTIFY_URLS").split(",") if u.strip()])

    # Agent / AI integration
    agent_api_keys: list[str] = field(default_factory=lambda: [
        k.strip() for k in _env("AGENT_API_KEYS").split(",") if k.strip()])
    llm_provider: str = field(default_factory=lambda: _env("LLM_PROVIDER", "none").lower())
    llm_base_url: str = field(default_factory=lambda: _env("LLM_BASE_URL", "http://ollama:11434/v1"))
    llm_model: str = field(default_factory=lambda: _env("LLM_MODEL", ""))
    llm_api_key: str = field(default_factory=lambda: _env("LLM_API_KEY"))

    def load_yaml(self, path: str) -> dict:
        with open(path, "r", encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}


settings = Settings()
