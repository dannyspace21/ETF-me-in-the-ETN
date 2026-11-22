"""Run fund discovery against the live listing sources and write the result to a
JSON snapshot that ships with the app (config/discovered.json).

The app loads the snapshot on first start, so it tracks every known leveraged
fund immediately, even before (or without) its own first internet scan.
A GitHub Actions workflow (.github/workflows/discovery-snapshot.yml) refreshes
it daily.

    python scripts/snapshot_universe.py config/discovered.json
"""
import asyncio
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import discovery, net  # noqa: E402

MIN_FUNDS = 150  # sanity floor: fewer than this means a source broke, don't overwrite a good snapshot


async def main(out: str) -> int:
    async with net.client(60) as c:
        res = await discovery.discover(c)
    rows = []
    for f in sorted(res.funds.values(), key=lambda f: f["ticker"]):
        rows.append({k: (sorted(v) if isinstance(v, set) else v) for k, v in f.items() if v not in (None, [], "")})

    print(f"sources ok: {res.ok}")
    for k, v in res.errors.items():
        print(f"  {k}: {v}")
    print(f"raw listings: {res.raw_counts}")
    print(f"leveraged funds: {len(rows)}")
    for key in ("country", "direction", "lev", "type", "group", "issuer"):
        print(f"  by {key}: {dict(Counter(str(r.get(key)) for r in rows).most_common(25))}")
    print("sample:")
    for r in rows[:: max(1, len(rows) // 40)]:
        print(f"  {r['ticker']:12} {r['lev']:>4}x {r['direction']:5} {r['type']} {r['group']:12} {r['name'][:70]}")

    if len(rows) < MIN_FUNDS:
        print(f"ERROR: only {len(rows)} funds found (< {MIN_FUNDS}); keeping the existing snapshot")
        return 1
    payload = {"generated": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
               "sources_ok": res.ok, "raw_counts": res.raw_counts, "count": len(rows)}
    with open(out, "w", encoding="utf-8") as fh:
        # one fund per line keeps git diffs readable when funds list or close
        fh.write(json.dumps(payload)[:-1] + ',\n"funds": [\n')
        fh.write(",\n".join(json.dumps(r, ensure_ascii=False) for r in rows))
        fh.write("\n]}\n")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "config/discovered.json")))
