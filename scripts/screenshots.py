"""Regenerate README screenshots.  Usage:
    DEMO_MODE=1 uvicorn app.main:app --port 8000 &
    python scripts/screenshots.py http://localhost:8000
(set CHROME_PATH to use an already-installed Chromium)
"""
import os
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
OUT = Path(__file__).resolve().parent.parent / "docs" / "screenshots"
OUT.mkdir(parents=True, exist_ok=True)

with sync_playwright() as p:
    # post an example agent forecast through the public API so the AI panel isn't empty
    import json, urllib.request
    req = urllib.request.Request(BASE + "/api/agent/forecasts", method="POST", headers={"Content-Type": "application/json"},
        data=json.dumps({"agent": "example-agent (via API)", "ticker": "TQQQ", "horizon_days": 252, "target_price": 60,
                         "probability": 55, "stance": "accumulate",
                         "rationale": "Down >50% from the 5y high. Past 50%+ crashes recovered within ~6-18 months of the bottom. "
                                      "Scale in: 1/3 now, 1/3 at Code Brown ($36), 1/3 at the $25 all-in target."}).encode())
    urllib.request.urlopen(req).read()
    b = p.chromium.launch(executable_path=os.environ.get("CHROME_PATH") or None)
    pg = b.new_page(viewport={"width": 1440, "height": 1000}, device_scale_factor=2)
    pg.goto(BASE + "/#/"); pg.wait_for_selector("#board tbody tr[data-t]"); pg.wait_for_timeout(500)
    pg.screenshot(path=str(OUT / "board.png"))
    pg.goto(BASE + "/#/TQQQ"); pg.wait_for_selector("#pchart svg"); pg.wait_for_timeout(500)
    pg.screenshot(path=str(OUT / "detail-tqqq-full.png"), full_page=True)
    ph = b.new_page(viewport={"width": 390, "height": 844}, device_scale_factor=2)
    ph.goto(BASE + "/#/"); ph.wait_for_selector("#board tbody tr[data-t]"); ph.wait_for_timeout(400)
    ph.screenshot(path=str(OUT / "mobile.png"))
    ic = b.new_page(viewport={"width": 256, "height": 256}, device_scale_factor=2)
    ic.goto(BASE + "/static/icon.svg"); ic.screenshot(path=str(OUT / "icon.png"), omit_background=True)
    b.close()
print("saved to", OUT)
