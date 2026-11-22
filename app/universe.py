from __future__ import annotations

from dataclasses import dataclass, asdict

from .config import settings


@dataclass(frozen=True)
class Fund:
    ticker: str
    lev: int
    type: str
    group: str
    issuer: str
    under: str

    def to_dict(self) -> dict:
        d = asdict(self)
        d["name"] = f"{self.lev}x {self.under}"
        return d


def load_universe(path: str | None = None) -> list[Fund]:
    data = settings.load_yaml(path or settings.universe_file)
    out, seen = [], set()
    for row in data.get("funds", []):
        t = str(row["ticker"]).upper()
        if t in seen:
            continue
        seen.add(t)
        out.append(Fund(ticker=t, lev=int(row.get("lev", 3)), type=str(row.get("type", "ETF")).upper(),
                        group=str(row.get("group", "broad")), issuer=str(row.get("issuer", "")),
                        under=str(row.get("under", ""))))
    return out
