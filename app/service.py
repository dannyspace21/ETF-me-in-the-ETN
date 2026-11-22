"""Refresh loop + alert engine. Owns the in-memory board cache."""
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta, timezone

import httpx

from . import analytics
from .config import settings
from .providers import fetch_history
from .store import Store, now_iso
from .universe import Fund, load_universe

log = logging.getLogger("etfme.service")


class Service:
    def __init__(self, store: Store):
        self.store = store
        self.universe: list[Fund] = load_universe()
        cfg = settings.load_yaml(settings.alerts_file)
        self.tiers: list[dict] = sorted(cfg.get("tiers", []), key=lambda t: t["min_dd"])
        self.board: dict[str, dict] = {}
        self.refreshing = False
        self.last_refresh: str | None = None
        if not self.store.kv_get("seeded"):
            for t in cfg.get("seed_targets", []):
                self.store.add_target(t["ticker"], float(t["price"]), t.get("direction", "below"), t.get("label", ""))
            self.store.kv_set("seeded", "1")

    def fund(self, ticker: str) -> Fund | None:
        return next((f for f in self.universe if f.ticker == ticker.upper()), None)

    def tier(self, key: str) -> dict:
        return next((t for t in self.tiers if t["key"] == key), self.tiers[0])

    # ---------------------------------------------------------------- data
    def rebuild(self, ticker: str) -> dict:
        f = self.fund(ticker)
        meta = self.store.get_meta(ticker) or {}
        s = analytics.summarize(f.to_dict(), self.store.prices(ticker), self.tiers, settings.peak_lookback_years)
        s.update(provider=meta.get("provider"), last_refresh=meta.get("last_refresh"), error=meta.get("last_error"))
        self.board[ticker] = s
        return s

    async def refresh_one(self, client: httpx.AsyncClient, f: Fund) -> None:
        last = self.store.last_date(f.ticker)
        start = (date.fromisoformat(last) - timedelta(days=10)) if last else date.fromisoformat(settings.history_start)
        try:
            provider, bars = await fetch_history(client, f.ticker, start)
            self.store.upsert_prices(f.ticker, bars)
            self.store.set_meta(f.ticker, provider=provider, last_refresh=now_iso(), last_error=None)
        except Exception as e:  # noqa: BLE001
            log.warning("refresh %s failed: %s", f.ticker, e)
            self.store.set_meta(f.ticker, last_refresh=now_iso(), last_error=str(e)[:400])
        s = self.rebuild(f.ticker)
        if s.get("has_data"):
            await self.evaluate_alerts(s)

    async def refresh_all(self) -> None:
        if self.refreshing:
            return
        self.refreshing = True
        try:
            sem = asyncio.Semaphore(6)
            async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
                async def one(f):
                    async with sem:
                        await self.refresh_one(client, f)
                await asyncio.gather(*(one(f) for f in self.universe))
            self.last_refresh = now_iso()
            log.info("refreshed %d funds", len(self.universe))
        finally:
            self.refreshing = False

    def warm_from_cache(self) -> None:
        for f in self.universe:
            self.rebuild(f.ticker)

    async def loop(self) -> None:
        while True:
            try:
                await self.refresh_all()
            except Exception:  # noqa: BLE001
                log.exception("refresh loop error")
            await asyncio.sleep(max(1, settings.refresh_minutes) * 60)

    # ---------------------------------------------------------------- alerts
    async def evaluate_alerts(self, s: dict) -> None:
        t = s["ticker"]
        meta = self.store.get_meta(t) or {}
        old_key = meta.get("tier")
        new_key = analytics.sticky_tier(-s["drawdown"], old_key, self.tiers)["key"]
        if old_key is None:  # first time we see this fund: backfill recent history quietly (no push spam)
            self.store.set_meta(t, tier=new_key)
            for d, frm, to, px, dd in analytics.tier_history(self.store.prices(t), self.tiers,
                                                            settings.peak_lookback_years):
                worse = analytics.tier_index(to, self.tiers) > analytics.tier_index(frm, self.tiers)
                nt = self.tier(to)
                self.store.add_alert(ts=f"{d}T21:00:00+00:00", ticker=t, kind="tier", severity=to,
                                     title=f"{nt['emoji']} {t} {'entered' if worse else 'recovered to'} {nt['label']}",
                                     message=f"{t} @ ${px:.2f}, {dd:.1f}% off the high. {nt['blurb']} (backfilled)",
                                     price=px, drawdown=dd)
        elif old_key != new_key:
            self.store.set_meta(t, tier=new_key)
            worse = analytics.tier_index(new_key, self.tiers) > analytics.tier_index(old_key, self.tiers)
            nt = self.tier(new_key)
            title = f"{nt['emoji']} {t} {'entered' if worse else 'recovered to'} {nt['label']}"
            msg = (f"{t} @ ${s['price']:.2f} is {s['drawdown']:.1f}% from its ${s['ath']:.2f} high "
                   f"({s['ath_date']}). {nt['blurb']}")
            await self.emit(ticker=t, kind="tier", severity=new_key, title=title, message=msg,
                            price=s["price"], drawdown=s["drawdown"])
        for tgt in self.store.targets(t):
            hit = s["price"] <= tgt["price"] if tgt["direction"] == "below" else s["price"] >= tgt["price"]
            if hit and not tgt["triggered_at"]:
                self.store.x("UPDATE targets SET triggered_at=? WHERE id=?", (now_iso(), tgt["id"]))
                await self.emit(ticker=t, kind="target", severity="allin",
                                title=f"🎯 {t} hit your ${tgt['price']:.2f} target",
                                message=f"{tgt['label'] or 'Price target'} — {t} @ ${s['price']:.2f} "
                                        f"({s['drawdown']:.1f}% from high).",
                                price=s["price"], drawdown=s["drawdown"])
            elif not hit and tgt["triggered_at"]:
                self.store.x("UPDATE targets SET triggered_at=NULL WHERE id=?", (tgt["id"],))

    async def emit(self, **a) -> None:
        self.store.add_alert(**a)
        if settings.notify_urls:
            await notify(a["title"], a["message"])


async def notify(title: str, message: str) -> None:
    """Fan out to Discord / Slack / ntfy / generic JSON webhooks."""
    async with httpx.AsyncClient(timeout=10) as client:
        for url in settings.notify_urls:
            try:
                if "discord.com" in url:
                    await client.post(url, json={"content": f"**{title}**\n{message}"})
                elif "hooks.slack.com" in url:
                    await client.post(url, json={"text": f"*{title}*\n{message}"})
                elif "ntfy" in url:
                    await client.post(url, content=message.encode(), headers={"Title": title.encode("ascii", "ignore").decode()})
                else:
                    await client.post(url, json={"title": title, "message": message,
                                                  "ts": datetime.now(timezone.utc).isoformat()})
            except Exception as e:  # noqa: BLE001
                log.warning("notify %s failed: %s", url, e)
