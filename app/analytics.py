"""All the number crunching: drawdowns, alert tiers, crash episodes, buy-the-dip
scorecards and a bootstrap Monte Carlo forecast. Pure functions, no I/O."""
from __future__ import annotations

import zlib
from typing import Any

import numpy as np

TRADING_DAYS = 252


# ---------------------------------------------------------------- tiers
def tier_for(dd_pct: float, tiers: list[dict]) -> dict:
    """dd_pct is a positive drawdown percentage (e.g. 51.2 for -51.2%)."""
    chosen = tiers[0]
    for t in tiers:
        if dd_pct >= t["min_dd"]:
            chosen = t
    return chosen


def sticky_tier(dd_pct: float, prev_key: str | None, tiers: list[dict], hysteresis: float = 3.0) -> dict:
    """Like tier_for, but only step *down* to a milder tier once price has recovered
    ``hysteresis`` points past the boundary — stops alert flapping around a line."""
    t = tier_for(dd_pct, tiers)
    if prev_key is None:
        return t
    pi = tier_index(prev_key, tiers)
    if tier_index(t["key"], tiers) < pi and dd_pct > tiers[pi]["min_dd"] - hysteresis:
        return tiers[pi]
    return t


def tier_index(key: str, tiers: list[dict]) -> int:
    for i, t in enumerate(tiers):
        if t["key"] == key:
            return i
    return 0


def _pct(a: float, b: float) -> float | None:
    if a is None or b is None or b == 0:
        return None
    return (a / b - 1.0) * 100.0


def _r(x, n=2):
    return None if x is None or (isinstance(x, float) and not np.isfinite(x)) else round(float(x), n)


def _downsample(values: list[float], n: int = 80) -> list[float]:
    if len(values) <= n:
        return [round(v, 4) for v in values]
    idx = np.linspace(0, len(values) - 1, n).round().astype(int)
    return [round(values[i], 4) for i in idx]


# ---------------------------------------------------------------- summary
def peak_window(n: int, lookback_years: float) -> int:
    """Index where the peak-search window starts."""
    return 0 if not lookback_years else max(0, n - int(lookback_years * TRADING_DAYS))


def tier_history(rows: list[dict], tiers: list[dict], lookback_years: float, days: int = 60) -> list[tuple]:
    """(date, tier_key, price, drawdown) for each tier *change* over the last ``days`` bars."""
    close = np.array([r["close"] for r in rows], dtype=float)
    out, prev = [], None
    for i in range(max(1, len(close) - days), len(close)):
        lo = peak_window(i + 1, lookback_years)
        dd = (close[i] / close[lo:i + 1].max() - 1) * 100
        key = sticky_tier(-dd, prev, tiers)["key"]
        if prev is not None and key != prev:
            out.append((rows[i]["date"], prev, key, float(close[i]), round(float(dd), 2)))
        prev = key
    return out


def summarize(fund: dict, rows: list[dict], tiers: list[dict], lookback_years: float = 0) -> dict[str, Any]:
    out = dict(fund)
    if len(rows) < 2:
        out.update(has_data=False)
        return out
    dates = [r["date"] for r in rows]
    close = np.array([r["close"] for r in rows], dtype=float)
    adj = np.array([r["adj"] for r in rows], dtype=float)
    price, prev = close[-1], close[-2]
    lo = peak_window(len(close), lookback_years)
    ath_i = lo + int(np.argmax(close[lo:]))
    ath = close[ath_i]
    dd = (price / ath - 1) * 100
    low_since_ath = float(close[ath_i:].min())
    w52 = close[-TRADING_DAYS:]
    hi52 = float(w52.max())

    # Return since 2011 (or since inception if younger)
    start_i = next((i for i, d in enumerate(dates) if d >= "2011-01-01"), 0)
    years = max((len(adj) - 1 - start_i) / TRADING_DAYS, 1e-9)
    total = (adj[-1] / adj[start_i] - 1) * 100
    cagr = ((adj[-1] / adj[start_i]) ** (1 / years) - 1) * 100 if years > 0.5 else None

    tier = tier_for(-dd, tiers)
    ti = tier_index(tier["key"], tiers)
    nxt = tiers[ti + 1] if ti + 1 < len(tiers) else None
    next_price = ath * (1 - nxt["min_dd"] / 100) if nxt else None

    out.update(
        has_data=True, price=_r(price, 4), prev_close=_r(prev, 4), as_of=dates[-1],
        change_1d=_r(_pct(price, prev)),
        change_1m=_r(_pct(price, close[-22] if len(close) > 22 else close[0])),
        change_ytd=_r(_pct(price, next((close[i - 1] for i, d in enumerate(dates)
                                        if d[:4] == dates[-1][:4] and i > 0), close[0]))),
        ath=_r(ath, 4), ath_date=dates[ath_i], drawdown=_r(dd), low_since_ath=_r(low_since_ath, 4),
        max_drawdown_since_ath=_r((low_since_ath / ath - 1) * 100),
        high_52w=_r(hi52, 4), drawdown_52w=_r((price / hi52 - 1) * 100),
        since_label="since 2011" if dates[start_i] <= "2011-01-31" else f"since {dates[start_i][:4]}",
        since_start_date=dates[start_i], total_return=_r(total, 1), cagr=_r(cagr),
        peak_basis=f"{lookback_years:g}y high" if lookback_years else "all-time high",
        all_time_high=_r(float(close.max()), 4),
        tier=tier["key"], tier_index=ti,
        next_tier=nxt["key"] if nxt else None, next_tier_price=_r(next_price, 4),
        pct_to_next_tier=_r(_pct(next_price, price)) if next_price else None,
        spark=_downsample(close[-TRADING_DAYS:].tolist(), 60),
        first_date=dates[0], bars=len(rows),
    )
    return out


# ---------------------------------------------------------------- crash episodes
def episodes(rows: list[dict], threshold_pct: float = 50.0) -> list[dict]:
    """Every time the fund fell ``threshold_pct``+ from a running peak.

    For each episode: the peak, the trough, how long it took to make a new high,
    and what you'd have made buying (a) the day it first crossed the threshold
    — something you can actually do — and (b) the exact bottom (hindsight)."""
    if len(rows) < 2:
        return []
    dates = [r["date"] for r in rows]
    close = np.array([r["close"] for r in rows], dtype=float)
    adj = np.array([r["adj"] for r in rows], dtype=float)
    n = len(close)
    th = threshold_pct / 100.0

    def fwd(i: int, days: int):
        j = i + days
        return _r((adj[j] / adj[i] - 1) * 100, 1) if j < n else None

    eps, peak_i, cur = [], 0, None
    for i in range(1, n):
        if cur is None:
            if close[i] >= close[peak_i]:
                peak_i = i
            elif close[i] <= close[peak_i] * (1 - th):
                cur = {"peak_i": peak_i, "cross_i": i, "trough_i": i}
        else:
            if close[i] < close[cur["trough_i"]]:
                cur["trough_i"] = i
            if close[i] >= close[cur["peak_i"]]:
                cur["recover_i"] = i
                eps.append(cur)
                cur, peak_i = None, i
    if cur is not None:
        eps.append(cur)

    out = []
    for e in eps:
        p, c, t, rcv = e["peak_i"], e["cross_i"], e["trough_i"], e.get("recover_i")
        out.append(dict(
            peak_date=dates[p], peak=_r(close[p], 4),
            cross_date=dates[c], cross_price=_r(close[c], 4),
            trough_date=dates[t], trough=_r(close[t], 4),
            max_drawdown=_r((close[t] / close[p] - 1) * 100, 1),
            days_peak_to_trough=t - p,
            recovered_date=dates[rcv] if rcv else None,
            trading_days_to_recover=(rcv - t) if rcv else None,
            ongoing=rcv is None,
            ret_1y_from_cross=fwd(c, TRADING_DAYS), ret_2y_from_cross=fwd(c, 2 * TRADING_DAYS),
            ret_1y_from_trough=fwd(t, TRADING_DAYS), ret_2y_from_trough=fwd(t, 2 * TRADING_DAYS),
            ret_to_now_from_trough=_r((adj[-1] / adj[t] - 1) * 100, 1),
            ret_to_now_from_cross=_r((adj[-1] / adj[c] - 1) * 100, 1),
        ))
    return out


def tier_scorecard(rows: list[dict], tiers: list[dict]) -> list[dict]:
    """Historically: if you bought the first day each tier was hit, how did it go?"""
    card = []
    for t in tiers:
        if t["min_dd"] <= 0:
            continue
        eps = episodes(rows, t["min_dd"])
        r1 = [e["ret_1y_from_cross"] for e in eps if e["ret_1y_from_cross"] is not None]
        r2 = [e["ret_2y_from_cross"] for e in eps if e["ret_2y_from_cross"] is not None]
        card.append(dict(
            tier=t["key"], min_dd=t["min_dd"], events=len(eps),
            median_1y=_r(float(np.median(r1)), 1) if r1 else None,
            median_2y=_r(float(np.median(r2)), 1) if r2 else None,
            win_rate_1y=_r(100 * sum(x > 0 for x in r1) / len(r1), 0) if r1 else None,
            best_1y=_r(max(r1), 1) if r1 else None, worst_1y=_r(min(r1), 1) if r1 else None,
        ))
    return card


# ---------------------------------------------------------------- Monte Carlo
def monte_carlo(rows: list[dict], *, horizon: int = TRADING_DAYS, paths: int = 3000, block: int = 10,
                lookback_years: float | None = None, targets: list[dict] | None = None,
                seed_key: str = "") -> dict:
    """Stationary block bootstrap of the fund's OWN daily log returns.

    Because it resamples the leveraged fund's realised returns (not the index ×3),
    volatility decay, fees and fat tails come along for free. It is a statistical
    sanity check, not a crystal ball."""
    if len(rows) < 260:
        return {"available": False, "reason": "need at least ~1 year of history"}
    adj = np.array([r["adj"] for r in rows], dtype=float)
    price = float(rows[-1]["close"])
    ath = float(max(r["close"] for r in rows))
    lr = np.diff(np.log(adj))
    if lookback_years:
        lr = lr[-int(lookback_years * TRADING_DAYS):]
    lr = lr[np.isfinite(lr)]
    m = len(lr)
    rng = np.random.default_rng(zlib.crc32((seed_key + rows[-1]["date"]).encode()))
    nblocks = int(np.ceil(horizon / block))
    starts = rng.integers(0, m - block, size=(paths, nblocks))
    idx = (starts[:, :, None] + np.arange(block)[None, None, :]).reshape(paths, -1)[:, :horizon]
    sim = price * np.exp(np.cumsum(lr[idx], axis=1))

    qs = [5, 25, 50, 75, 95]
    step = max(1, horizon // 52)
    days = list(range(0, horizon, step)) + [horizon - 1]
    fan = {f"p{q}": [round(price, 4)] + [round(float(np.percentile(sim[:, d], q)), 4) for d in days] for q in qs}
    term = sim[:, -1]
    mins, maxs = sim.min(axis=1), sim.max(axis=1)
    tgt_out = []
    for t in targets or []:
        tp = float(t["price"])
        hit = (mins <= tp) if t.get("direction", "below") == "below" else (maxs >= tp)
        tgt_out.append(dict(id=t.get("id"), price=tp, direction=t.get("direction", "below"),
                            label=t.get("label"), prob_touch=_r(100 * hit.mean(), 1)))
    return dict(
        available=True, horizon_days=horizon, paths=paths, start_price=_r(price, 4),
        fan_days=[0] + [d + 1 for d in days], fan=fan,
        terminal={f"p{q}": _r(float(np.percentile(term, q)), 4) for q in qs},
        prob_up=_r(100 * (term > price).mean(), 1),
        prob_reclaim_ath=_r(100 * (maxs >= ath).mean(), 1),
        prob_double=_r(100 * (maxs >= 2 * price).mean(), 1),
        prob_drop_another_30=_r(100 * (mins <= 0.7 * price).mean(), 1),
        expected_max_drawdown=_r(float(np.median((mins / price - 1) * 100)), 1),
        targets=tgt_out, method="stationary block bootstrap of daily log returns", block=block,
    )
