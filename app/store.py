"""Tiny SQLite persistence layer. One file, lives on the /data volume."""
from __future__ import annotations

import os
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS prices (ticker TEXT, date TEXT, close REAL, adj REAL, PRIMARY KEY (ticker, date))
  WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS funds (
  ticker TEXT PRIMARY KEY, name TEXT, lev REAL, direction TEXT, type TEXT, grp TEXT, issuer TEXT, under TEXT,
  country TEXT, currency TEXT, exchange TEXT, isin TEXT, sources TEXT, first_seen TEXT, last_seen TEXT,
  status TEXT DEFAULT 'active', new_listing INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS meta (ticker TEXT PRIMARY KEY, provider TEXT, last_refresh TEXT, last_error TEXT, tier TEXT);
CREATE TABLE IF NOT EXISTS targets (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ticker TEXT, price REAL, direction TEXT, label TEXT,
  created_at TEXT, triggered_at TEXT);
CREATE TABLE IF NOT EXISTS alerts (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, ticker TEXT, kind TEXT, severity TEXT,
  title TEXT, message TEXT, price REAL, drawdown REAL);
CREATE TABLE IF NOT EXISTS forecasts (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, agent TEXT, ticker TEXT, horizon_days INTEGER,
  target_price REAL, probability REAL, stance TEXT, rationale TEXT);
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS watchlist (ticker TEXT PRIMARY KEY, added_at TEXT);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class Store:
    def __init__(self, path: str):
        if path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        with self.lock:
            self.db.executescript(SCHEMA)
            self.db.execute("PRAGMA journal_mode=WAL")

    # ---------------------------------------------------------------- helpers
    def q(self, sql: str, args: Iterable[Any] = ()) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self.db.execute(sql, tuple(args)).fetchall()]

    def x(self, sql: str, args: Iterable[Any] = ()) -> int:
        with self.lock:
            cur = self.db.execute(sql, tuple(args))
            self.db.commit()
            return cur.lastrowid or 0

    # ---------------------------------------------------------------- prices
    def upsert_prices(self, ticker: str, bars) -> None:
        with self.lock:
            self.db.executemany("INSERT OR REPLACE INTO prices VALUES (?,?,?,?)",
                                [(ticker, b.date, b.close, b.adj) for b in bars])
            self.db.commit()

    def prices(self, ticker: str) -> list[dict]:
        return self.q("SELECT date, close, adj FROM prices WHERE ticker=? ORDER BY date", (ticker,))

    def delete_prices(self, ticker: str) -> None:
        self.x("DELETE FROM prices WHERE ticker=?", (ticker,))

    def closes_since(self, ticker: str, start: str) -> dict[str, float]:
        return {r["date"]: r["close"] for r in
                self.q("SELECT date, close FROM prices WHERE ticker=? AND date>=?", (ticker, start))}

    def last_date(self, ticker: str) -> str | None:
        r = self.q("SELECT MAX(date) d FROM prices WHERE ticker=?", (ticker,))
        return r[0]["d"] if r else None

    # ---------------------------------------------------------------- discovered funds
    FUND_COLS = ("ticker", "name", "lev", "direction", "type", "grp", "issuer", "under", "country", "currency",
                 "exchange", "isin", "sources", "first_seen", "last_seen", "status", "new_listing")

    def upsert_fund(self, f: dict) -> None:
        row = {**f, "grp": f.get("group", f.get("grp"))}
        if isinstance(row.get("sources"), (set, list, tuple)):
            row["sources"] = ",".join(sorted(row["sources"]))
        self.x(f"INSERT OR REPLACE INTO funds ({','.join(self.FUND_COLS)}) VALUES ({','.join('?' * len(self.FUND_COLS))})",
               [row.get(c) for c in self.FUND_COLS])

    def funds(self, status: str | None = None) -> list[dict]:
        rows = self.q("SELECT * FROM funds" + (" WHERE status=?" if status else "") + " ORDER BY ticker",
                      (status,) if status else ())
        for r in rows:
            r["group"] = r.pop("grp")
        return rows

    def get_fund(self, ticker: str) -> dict | None:
        r = self.q("SELECT * FROM funds WHERE ticker=?", (ticker.upper(),))
        if r:
            r[0]["group"] = r[0].pop("grp")
        return r[0] if r else None

    # ---------------------------------------------------------------- watchlist
    def watchlist(self) -> list[str]:
        return [r["ticker"] for r in self.q("SELECT ticker FROM watchlist ORDER BY added_at")]

    def watch(self, ticker: str, on: bool = True) -> None:
        if on:
            self.x("INSERT OR IGNORE INTO watchlist VALUES (?,?)", (ticker.upper(), now_iso()))
        else:
            self.x("DELETE FROM watchlist WHERE ticker=?", (ticker.upper(),))

    def provider_stats(self) -> list[dict]:
        return self.q("SELECT provider, COUNT(*) n, SUM(last_error IS NOT NULL) errors FROM meta GROUP BY provider")

    # ---------------------------------------------------------------- meta
    def set_meta(self, ticker: str, **kw) -> None:
        cur = self.get_meta(ticker) or {"ticker": ticker, "provider": None, "last_refresh": None,
                                         "last_error": None, "tier": None}
        cur.update(kw)
        self.x("INSERT OR REPLACE INTO meta VALUES (?,?,?,?,?)",
               (ticker, cur["provider"], cur["last_refresh"], cur["last_error"], cur["tier"]))

    def get_meta(self, ticker: str) -> dict | None:
        r = self.q("SELECT * FROM meta WHERE ticker=?", (ticker,))
        return r[0] if r else None

    def kv_get(self, k: str) -> str | None:
        r = self.q("SELECT v FROM kv WHERE k=?", (k,))
        return r[0]["v"] if r else None

    def kv_set(self, k: str, v: str) -> None:
        self.x("INSERT OR REPLACE INTO kv VALUES (?,?)", (k, v))

    # ---------------------------------------------------------------- targets
    def add_target(self, ticker: str, price: float, direction: str, label: str) -> int:
        return self.x("INSERT INTO targets (ticker, price, direction, label, created_at) VALUES (?,?,?,?,?)",
                      (ticker.upper(), price, direction, label, now_iso()))

    def targets(self, ticker: str | None = None) -> list[dict]:
        if ticker:
            return self.q("SELECT * FROM targets WHERE ticker=? ORDER BY price DESC", (ticker.upper(),))
        return self.q("SELECT * FROM targets ORDER BY ticker, price DESC")

    def delete_target(self, tid: int) -> None:
        self.x("DELETE FROM targets WHERE id=?", (tid,))

    # ---------------------------------------------------------------- alerts
    def add_alert(self, **a) -> int:
        return self.x("INSERT INTO alerts (ts, ticker, kind, severity, title, message, price, drawdown) "
                      "VALUES (?,?,?,?,?,?,?,?)",
                      (a.get("ts") or now_iso(), a["ticker"], a["kind"], a["severity"], a["title"], a["message"],
                       a.get("price"), a.get("drawdown")))

    def alerts(self, limit: int = 100, ticker: str | None = None) -> list[dict]:
        if ticker:
            return self.q("SELECT * FROM alerts WHERE ticker=? ORDER BY ts DESC, id DESC LIMIT ?", (ticker.upper(), limit))
        return self.q("SELECT * FROM alerts ORDER BY ts DESC, id DESC LIMIT ?", (limit,))

    # ---------------------------------------------------------------- agent forecasts
    def add_forecast(self, **f) -> int:
        return self.x("INSERT INTO forecasts (ts, agent, ticker, horizon_days, target_price, probability, stance, "
                      "rationale) VALUES (?,?,?,?,?,?,?,?)",
                      (now_iso(), f["agent"], f["ticker"].upper(), f.get("horizon_days"), f.get("target_price"),
                       f.get("probability"), f.get("stance"), f.get("rationale")))

    def forecasts(self, ticker: str | None = None, limit: int = 50) -> list[dict]:
        if ticker:
            return self.q("SELECT * FROM forecasts WHERE ticker=? ORDER BY id DESC LIMIT ?", (ticker.upper(), limit))
        return self.q("SELECT * FROM forecasts ORDER BY id DESC LIMIT ?", (limit,))
