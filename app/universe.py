"""The tracked universe = curated funds (config/universe.yaml) + everything the
discovery job has found (SQLite ``funds`` table), minus anything excluded."""
from __future__ import annotations

from dataclasses import asdict, dataclass

from . import exchanges as ex
from .config import settings


def fmt_lev(lev: float) -> str:
    return f"{lev:g}"


@dataclass(frozen=True)
class Fund:
    ticker: str
    lev: float
    type: str
    group: str
    issuer: str
    under: str
    direction: str = "long"       # long | short
    country: str = "US"
    currency: str = "USD"
    exchange: str = "US (NYSE / Nasdaq / Cboe)"
    full_name: str = ""
    source: str = "curated"       # curated | discovered
    first_seen: str | None = None

    @property
    def signed_lev(self) -> float:
        return -self.lev if self.direction == "short" else self.lev

    def to_dict(self) -> dict:
        d = asdict(self)
        d["name"] = f"{fmt_lev(self.lev)}x {'Bear ' if self.direction == 'short' else ''}{self.under}"
        return d


def _from_row(row: dict, source: str) -> Fund:
    t = str(row["ticker"]).upper()
    e = ex.exchange_of(t)
    return Fund(ticker=t, lev=float(row.get("lev", 3)), type=str(row.get("type", "ETF")).upper(),
                group=str(row.get("group") or row.get("grp") or "broad"), issuer=str(row.get("issuer") or ""),
                under=str(row.get("under") or ""), direction=str(row.get("direction") or "long").lower(),
                country=str(row.get("country") or e.country), currency=str(row.get("currency") or e.currency),
                exchange=str(row.get("exchange") or e.name), full_name=str(row.get("name") or ""),
                source=source, first_seen=row.get("first_seen"))


def load_curated(path: str | None = None) -> tuple[list[Fund], set[str]]:
    data = settings.load_yaml(path or settings.universe_file)
    out, seen = [], set()
    for row in data.get("funds", []):
        f = _from_row(row, "curated")
        if f.ticker not in seen:
            seen.add(f.ticker)
            out.append(f)
    return out, {str(t).upper() for t in data.get("exclude", []) or []}


def load_universe(path: str | None = None, discovered: list[dict] | None = None) -> list[Fund]:
    curated, excluded = load_curated(path)
    out = {f.ticker: f for f in curated if f.ticker not in excluded}
    for row in discovered or []:
        t = str(row["ticker"]).upper()
        if row.get("status") == "delisted":  # gone from every listing source: drop it, even if curated
            out.pop(t, None)
            continue
        if t in excluded:
            continue
        if t in out:  # curated metadata wins, but remember when discovery first saw it
            continue
        out[t] = _from_row(row, "discovered")
    return list(out.values())
