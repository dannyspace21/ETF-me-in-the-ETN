import yaml

from app import analytics
from app.config import ROOT

TIERS = sorted(yaml.safe_load(open(ROOT / "config" / "alerts.yaml"))["tiers"], key=lambda t: t["min_dd"])


def rows(prices, start="2020-01-01"):
    import datetime as dt
    d0 = dt.date.fromisoformat(start)
    return [{"date": (d0 + dt.timedelta(days=i)).isoformat(), "close": p, "adj": p} for i, p in enumerate(prices)]


def test_tier_ladder():
    assert analytics.tier_for(0, TIERS)["key"] == "chill"
    assert analytics.tier_for(25, TIERS)["key"] == "clench"
    assert analytics.tier_for(50.8, TIERS)["key"] == "shock"   # TQQQ 87 -> 42.75
    assert analytics.tier_for(71.3, TIERS)["key"] == "brown"   # TQQQ 87 -> 25
    assert analytics.tier_for(79, TIERS)["key"] == "allin"     # TQQQ 2022: 41 -> 8.65


def test_hysteresis_blocks_flapping():
    assert analytics.sticky_tier(39, "shock", TIERS)["key"] == "shock"    # only 1pt above line
    assert analytics.sticky_tier(36, "shock", TIERS)["key"] == "clench"   # clearly recovered
    assert analytics.sticky_tier(61, "shock", TIERS)["key"] == "brown"    # getting worse is instant


def test_summary_drawdown_and_next_tier():
    s = analytics.summarize({"ticker": "X"}, rows([10, 20, 87, 60, 42.75]), TIERS)
    assert s["ath"] == 87 and round(s["drawdown"], 1) == -50.9
    assert s["tier"] == "shock" and s["next_tier"] == "brown"
    assert abs(s["next_tier_price"] - 87 * 0.4) < 1e-6


def test_peak_lookback_ignores_ancient_highs():
    prices = [1000] + [10] * 2000 + [12]
    assert analytics.summarize({}, rows(prices), TIERS, lookback_years=0)["drawdown"] < -98
    assert analytics.summarize({}, rows(prices), TIERS, lookback_years=5)["drawdown"] == 0


def test_episodes_capture_crash_and_recovery():
    prices = [40, 41, 30, 20, 8.65, 12, 30, 45, 50]
    eps = analytics.episodes(rows(prices), 50)
    assert len(eps) == 1
    e = eps[0]
    assert e["peak"] == 41 and e["trough"] == 8.65 and e["cross_price"] == 20
    assert e["max_drawdown"] == -78.9 and not e["ongoing"]


def test_monte_carlo_shapes():
    import numpy as np
    rng = np.random.default_rng(0)
    p = 20 * np.exp(np.cumsum(rng.normal(0.0005, 0.03, 800)))
    mc = analytics.monte_carlo(rows(p.tolist()), paths=500, targets=[{"price": p[-1] * 0.5, "direction": "below"}])
    assert mc["available"] and len(mc["fan"]["p50"]) == len(mc["fan_days"])
    assert 0 <= mc["targets"][0]["prob_touch"] <= 100
