"""Refresh loop + alert engine. Owns the in-memory board cache."""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import date, datetime, timedelta, timezone

import httpx

from . import analytics, discovery, net
from .config import settings
from .providers import alpaca_batch, batch_provider_first, fetch_history
from .store import Store, now_iso
from .universe import Fund, fmt_lev, load_universe

SPLIT_TOLERANCE = 0.02  # stored vs. fresh close differing by >2% on the same day = split / restatement

log = logging.getLogger("etfme.service")


class Service:
    def __init__(self, store: Store):
        self.store = store
        self.universe: list[Fund] = []
        self._by_ticker: dict[str, Fund] = {}
        self.board: dict[str, dict] = {}
        self.reload_universe()
        self.discovery_running = False
        self.last_discovery: dict | None = None
        cfg = settings.load_yaml(settings.alerts_file)
        self.tiers: list[dict] = sorted(cfg.get("tiers", []), key=lambda t: t["min_dd"])
        self.refreshing = False
        self.last_refresh: str | None = None
        if not self.store.kv_get("seeded"):
            for t in cfg.get("seed_targets", []):
                self.store.add_target(t["ticker"], float(t["price"]), t.get("direction", "below"), t.get("label", ""))
            self.store.kv_set("seeded", "1")

    def reload_universe(self) -> list[Fund]:
        """Rebuild the universe from curated YAML + discovered funds. Returns funds that are new to it."""
        discovered = [] if settings.demo_mode else self.store.funds()
        new_u = load_universe(discovered=discovered)
        added = [f for f in new_u if f.ticker not in self._by_ticker]
        removed = set(self._by_ticker) - {f.ticker for f in new_u}
        self.universe = new_u
        self._by_ticker = {f.ticker: f for f in new_u}
        for t in removed:
            self.board.pop(t, None)
        return added

    def fund(self, ticker: str) -> Fund | None:
        return self._by_ticker.get(ticker.upper())

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

    def start_date(self, ticker: str) -> tuple[date, bool]:
        """(fetch start, is_backfill)."""
        last = self.store.last_date(ticker)
        if last:
            return date.fromisoformat(last) - timedelta(days=10), False
        return date.fromisoformat(settings.history_start), True

    def _split_detected(self, ticker: str, bars) -> bool:
        """Leveraged funds reverse-split all the time. If the overlap between what we stored and
        what the provider now says disagrees, the stored history is stale: refetch all of it."""
        if not bars:
            return False
        stored = self.store.closes_since(ticker, bars[0].date)
        for b in bars:
            old = stored.get(b.date)
            if old and b.close and abs(old / b.close - 1) > SPLIT_TOLERANCE:
                return True
        return False

    async def refresh_one(self, client: httpx.AsyncClient, f: Fund, pre: tuple[str, list] | None = None) -> None:
        start, backfill = self.start_date(f.ticker)
        try:
            if pre and pre[1]:
                provider, bars = pre
            else:
                provider, bars = await fetch_history(client, f.ticker, start, backfill=backfill,
                                                     skip=("alpaca",) if pre is not None else ())
            if not backfill and self._split_detected(f.ticker, bars):
                log.info("%s: split/restatement detected, refetching full history", f.ticker)
                provider, bars = await fetch_history(client, f.ticker, date.fromisoformat(settings.history_start),
                                                     backfill=True)
                self.store.delete_prices(f.ticker)
            self.store.upsert_prices(f.ticker, bars)
            self.store.set_meta(f.ticker, provider=provider, last_refresh=now_iso(), last_error=None)
        except Exception as e:  # noqa: BLE001
            log.warning("refresh %s failed: %s", f.ticker, e)
            self.store.set_meta(f.ticker, last_refresh=now_iso(), last_error=str(e)[:400])
        s = self.rebuild(f.ticker)
        if s.get("has_data"):
            await self.evaluate_alerts(s)

    async def prefetch_batched(self, client: httpx.AsyncClient, funds: list[Fund]) -> dict[str, tuple[str, list]]:
        """Alpaca returns 100 symbols per request, so the daily update of thousands of US funds
        is a handful of calls instead of thousands. Backfills go through the per-ticker chain."""
        if settings.demo_mode:
            return {}
        groups: dict[date, list[str]] = {}
        for f in funds:
            if batch_provider_first(f.ticker):
                start, backfill = self.start_date(f.ticker)
                if not backfill:
                    groups.setdefault(start, []).append(f.ticker)
        out: dict[str, tuple[str, list]] = {}
        for start, syms in groups.items():
            try:
                got = await alpaca_batch(client, syms, start)
            except Exception as e:  # noqa: BLE001
                log.warning("alpaca batch (%d symbols) failed: %s", len(syms), e)
                got = {}  # empty => these funds skip Alpaca and use the next provider
            for sym in syms:  # empty result => fall through to the other providers
                out[sym] = ("alpaca", got.get(sym, []))
        return out

    async def refresh_funds(self, funds: list[Fund]) -> None:
        sem = asyncio.Semaphore(max(1, settings.fetch_concurrency))
        async with net.client(30) as client:
            pre = await self.prefetch_batched(client, funds)

            async def one(f):
                async with sem:
                    await self.refresh_one(client, f, pre.get(f.ticker))
            await asyncio.gather(*(one(f) for f in funds))

    async def refresh_all(self) -> None:
        if self.refreshing:
            return
        self.refreshing = True
        try:
            await self.refresh_funds(list(self.universe))
            self.last_refresh = now_iso()
            log.info("refreshed %d funds", len(self.universe))
        finally:
            self.refreshing = False

    # ---------------------------------------------------------------- discovery
    async def run_discovery(self, client: httpx.AsyncClient | None = None) -> dict:
        """Find every leveraged ETF/ETN/ETP, add new ones, mark vanished ones delisted, alert on launches."""
        if self.discovery_running:
            return self.last_discovery or {}
        self.discovery_running = True
        try:
            own = client is None
            client = client or net.client(60)
            try:
                res = await discovery.discover(client)
            finally:
                if own:
                    await client.aclose()
            now = now_iso()
            booted = {s for s in discovery.SOURCES if self.store.kv_get(f"discovery_boot:{s}")}
            new_listings, added_rows = [], 0
            for t, f in res.funds.items():
                old = self.store.get_fund(t)
                # A fund is a *new listing* only if a source that had already done its baseline scan
                # just saw it for the first time. Everything found by a first scan is pre-existing.
                is_new = not old and bool(set(f["sources"]) & booted)
                row = {**f, "first_seen": old["first_seen"] if old else now, "last_seen": now, "status": "active",
                       "new_listing": (old or {}).get("new_listing") or int(is_new)}
                if old:
                    row["sources"] = set(f["sources"]) | set((old.get("sources") or "").split(",")) - {""}
                self.store.upsert_fund(row)
                if not old:
                    added_rows += 1
                    if is_new:
                        new_listings.append(t)
            # delist: gone from every exhaustive source that found it, for long enough
            ok_exhaustive = set(res.ok) & discovery.EXHAUSTIVE
            cutoff = (datetime.now(timezone.utc) - timedelta(days=settings.discovery_delist_days)).isoformat()
            delisted = []
            for row in self.store.funds("active"):
                srcs = set((row.get("sources") or "").split(",")) - {""}
                if row["ticker"] not in res.funds and srcs and srcs <= ok_exhaustive and (row["last_seen"] or "") < cutoff:
                    row["status"] = "delisted"
                    self.store.upsert_fund(row)
                    delisted.append(row["ticker"])
            for s in res.ok:
                self.store.kv_set(f"discovery_boot:{s}", now)
            added = self.reload_universe()
            for f in added:
                self.rebuild(f.ticker)
            for t in new_listings:
                f = self.fund(t)
                if f:
                    await self.emit(ticker=t, kind="new_fund", severity="new",
                                    title=f"🆕 New leveraged fund: {t}",
                                    message=f"{f.full_name or f.under} — {fmt_lev(f.lev)}x "
                                            f"{'bear' if f.direction == 'short' else 'bull'} {f.type} on "
                                            f"{f.exchange} ({f.currency}).")
            self.last_discovery = {
                "ts": now, "found": len(res.funds), "added": added_rows, "new_listings": new_listings,
                "delisted": delisted, "sources_ok": res.ok, "errors": res.errors, "raw_counts": res.raw_counts}
            self.store.kv_set("last_discovery", json.dumps(self.last_discovery))
            log.info("discovery: %d found, %d added (%d new listings), %d delisted",
                     len(res.funds), added_rows, len(new_listings), len(delisted))
            if added:
                asyncio.create_task(self.refresh_funds(added))
            return self.last_discovery
        finally:
            self.discovery_running = False

    async def discovery_loop(self) -> None:
        if settings.demo_mode or not settings.discovery_sources or settings.discovery_hours <= 0:
            return
        while True:
            try:
                await self.run_discovery()
            except Exception:  # noqa: BLE001
                log.exception("discovery error")
            await asyncio.sleep(settings.discovery_hours * 3600)

    def discovery_status(self) -> dict:
        if self.last_discovery is None:
            raw = self.store.kv_get("last_discovery")
            self.last_discovery = json.loads(raw) if raw else None
        counts: dict[str, dict[str, int]] = {"country": {}, "group": {}, "direction": {}, "lev": {}}
        for f in self.universe:
            for k, v in (("country", f.country), ("group", f.group), ("direction", f.direction),
                         ("lev", fmt_lev(f.lev))):
                counts[k][v] = counts[k].get(v, 0) + 1
        return {"enabled": not settings.demo_mode and bool(settings.discovery_sources) and settings.discovery_hours > 0,
                "running": self.discovery_running, "every_hours": settings.discovery_hours,
                "sources": settings.discovery_sources, "tracked": len(self.universe),
                "curated": sum(1 for f in self.universe if f.source == "curated"),
                "discovered": sum(1 for f in self.universe if f.source == "discovered"),
                "breakdown": counts, "last_run": self.last_discovery}

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

    def should_push(self, a: dict) -> bool:
        if a["kind"] == "new_fund":
            return settings.notify_new_funds
        if a["ticker"] in self.store.watchlist():  # starred funds always get through
            return True
        if a["kind"] == "tier" and settings.notify_min_tier:
            return analytics.tier_index(a["severity"], self.tiers) >= analytics.tier_index(settings.notify_min_tier,
                                                                                          self.tiers)
        return True

    async def emit(self, **a) -> None:
        self.store.add_alert(**a)
        if settings.notify_urls and self.should_push(a):
            await notify(a["title"], a["message"])


async def notify(title: str, message: str) -> None:
    """Fan out to Discord / Slack / ntfy / generic JSON webhooks."""
    async with net.client(10) as client:
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
