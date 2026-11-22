"""Deterministic synthetic prices for DEMO_MODE (offline screenshots, tests, kicking the tires).

TQQQ is pinned to hand-picked waypoints that mirror the shape of its real history
(2011 → 2018 Q4 → Covid → 2021 peak → 2022 −79% crash → 2026 peak → current slide);
every other fund is simulated off the same market factor with its own leverage,
beta and noise, so the board looks alive. NOT REAL MARKET DATA.
"""
from __future__ import annotations

import zlib
from datetime import date
from functools import lru_cache

import numpy as np

from . import Bar

END = date(2026, 11, 20)
TQQQ_WAYPOINTS = [
    ("2011-01-03", 0.75), ("2011-10-03", 0.48), ("2015-07-20", 3.70), ("2016-02-11", 2.60),
    ("2018-10-01", 7.90), ("2018-12-24", 4.40), ("2020-02-19", 15.2), ("2020-03-16", 7.40),
    ("2021-11-19", 41.0), ("2022-12-28", 8.65), ("2024-07-10", 42.5), ("2024-08-05", 27.5),
    ("2025-04-08", 17.6), ("2026-06-15", 87.0), ("2026-11-20", 42.75),
]
GROUP = {  # beta to the "Nasdaq-ish" factor, idiosyncratic daily vol
    "broad": (0.85, 0.004), "sector": (0.95, 0.007), "intl": (0.6, 0.008), "rates": (-0.15, 0.006),
    "commodity": (0.3, 0.011), "single-stock": (1.25, 0.014), "crypto": (1.2, 0.02),
}


@lru_cache(maxsize=1)
def _calendar() -> np.ndarray:
    return _bdays()


def _bdays() -> np.ndarray:
    d = np.arange(np.datetime64("2011-01-03"), np.datetime64(END.isoformat()) + 1, dtype="datetime64[D]")
    return d[np.is_busday(d)]


@lru_cache(maxsize=1)
def _tqqq_log() -> np.ndarray:
    days = _calendar()
    rng = np.random.default_rng(42)
    logp = np.empty(len(days))
    idx = [int(np.searchsorted(days, np.datetime64(d))) for d, _ in TQQQ_WAYPOINTS]
    vals = [np.log(v) for _, v in TQQQ_WAYPOINTS]
    for (i0, v0), (i1, v1) in zip(zip(idx, vals), zip(idx[1:], vals[1:])):
        n = i1 - i0
        w = np.cumsum(rng.normal(0, 0.019, n))
        bridge = w - np.arange(1, n + 1) / n * w[-1]
        logp[i0 + 1:i1 + 1] = v0 + (v1 - v0) * np.arange(1, n + 1) / n + bridge
    logp[idx[0]] = vals[0]
    return logp


@lru_cache(maxsize=256)
def _series(ticker: str, lev: int, group: str) -> tuple[tuple[str, float], ...]:
    days = _calendar()
    lp = _tqqq_log()
    if ticker == "TQQQ":
        return tuple((str(d), float(np.exp(v))) for d, v in zip(days, lp))
    market = np.diff(lp) / 3.0  # de-lever the factor
    rng = np.random.default_rng(zlib.crc32(ticker.encode()))
    beta, idio = GROUP.get(group, (0.9, 0.01))
    beta *= rng.uniform(0.8, 1.2)
    start = {"single-stock": 2900, "crypto": 3300}.get(group, int(rng.integers(0, 400)))
    under = beta * market[start:] + rng.normal(0.00012, idio, len(market) - start)
    rets = lev * (np.exp(under) - 1) - 0.0095 / 252
    path = np.cumprod(np.clip(1 + rets, 0.05, None))
    path *= rng.uniform(8, 60) / path[-1]
    path = np.concatenate([[path[0] / (1 + rets[0])], path])
    return tuple((str(d), float(p)) for d, p in zip(days[start:], path))


def demo_bars(ticker: str) -> list[Bar]:
    from ..universe import load_universe
    meta = {f.ticker: f for f in load_universe()}.get(ticker)
    lev, group = (meta.lev, meta.group) if meta else (3, "broad")
    return [Bar(d, round(p, 4), round(p, 4)) for d, p in _series(ticker, lev, group)]
