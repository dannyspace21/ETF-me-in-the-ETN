<p align="center"><img src="docs/screenshots/icon.png" width="128" alt="ETF Me in the ETN icon: a shocked cartoon poop, red crash line going in, green rocket line coming out"></p>

<h1 align="center">ETF Me in the ETN</h1>
<p align="center"><b>A crash tracker for every leveraged ETF, ETN and ETP, in the US and abroad.</b><br>
It finds the leveraged funds that trade, re-checks for new launches twice a day, and tells you when one has really pooped the bed, because that's when they bounce the hardest.</p>

---

## Why

Leveraged bulls crash hard, but the ones tracking big, durable indexes have come back harder. This app:

* **finds leveraged ETFs / ETNs / ETPs automatically**: every US listing (Nasdaq, NYSE, NYSE Arca, Cboe), plus London, Xetra, Paris, Amsterdam, Milan, Swiss, Toronto, Hong Kong, Tokyo, Korea, Taiwan, ASX and more. 1.25x and up, bull **and** bear, broad market, sectors, single stocks, crypto, commodities, rates, volatility. The US, Canada, Hong Kong, Taiwan and Korea come from the exchanges' own official listings; Europe, Australia and older Japanese funds come from a best-effort Yahoo search unless you add an EODHD key (see [discovery](#finding-every-leveraged-fund-discovery))
* **re-scans for new launches** every 12 hours and alerts you when a new leveraged fund shows up; funds that close are marked delisted
* pulls **real market data** from Alpaca, Yahoo, Stooq, EODHD, Tiingo, Polygon, FMP or Alpha Vantage, with automatic fallback
* puts each fund on an **alert ladder** based on its drop from the peak
* shows **every past 50%+ crash** and what buying it would have returned 1y and 2y later
* runs a **Monte Carlo forecast** on the fund's own returns, so volatility decay is baked in
* lets you set **price targets** ("TQQQ @ $25: back up the truck"), **star funds** for your watchlist, and **pings you** on Discord, Slack, ntfy or any webhook
* shows a **📡 Data connections** panel: which APIs are connected, how many funds each one serves, the last discovery scan and what's newly listed
* exposes an **agent API** so AI agents (local or hosted) can read the data and post their own forecasts

## Screenshots

> Screenshots use the built-in `DEMO_MODE` (synthetic data shaped like TQQQ's real history), so they look the same without internet access. Demo mode turns discovery off, so they only show the ~80 curated funds from `config/universe.yaml`; a live install tracks thousands.

**The board:** every fund ranked by how far it has fallen, alert-level counts on top, filters for bull/bear, ETF/ETN/ETP, leverage, group, market and new listings, a ☆ to star funds, and the alert feed and data-connections panel on the right.

![Board](docs/screenshots/board.png)

**Fund detail:** price with the crash ladder drawn on, drawdown underneath, crash history, alert-level scorecard, Monte Carlo fan, targets and AI agent takes.

![TQQQ detail](docs/screenshots/detail-tqqq-full.png)

**Phone**

<img src="docs/screenshots/mobile.png" width="320" alt="Mobile view">

## The alert ladder

Drawdown is measured from the highest close in the last `PEAK_LOOKBACK_YEARS` (default 5).

| Level | Drawdown | Meaning |
|---|---|---|
| 😎 Chillin' | < 20% | Business as usual. |
| 😬 Clench | ≥ 20% | Butt cheeks engaged. |
| 😱 Real Shock | ≥ 40% | Leveraged bull got tased. |
| 💩 Code Brown | ≥ 60% | It took a poop. Start shopping. |
| 🚀 ETF Me In The ETN | ≥ 75% | All the money the world has seen. |

The ladder applies to bear (inverse) funds too: for them a big drawdown means the underlying has rallied.

Edit `config/alerts.yaml` to change the levels, names or seeded price targets. Alerts use a 3-point buffer, so a fund sitting right on a line doesn't trigger an alert every day.

The 5-year window is deliberate. Funds that decay over time (oil, natural gas, some single stocks) have split-adjusted all-time highs from ten years ago, so measuring from those would leave them stuck at −99% forever. Set it to `0` to use the true all-time high.

## Quick start

```bash
git clone https://github.com/dannyspace21/ETF-me-in-the-ETN && cd ETF-me-in-the-ETN
cp .env.example .env            # optional
docker compose up -d --build    # http://localhost:8000
```

The first start scans the listing sources and downloads full daily history for every leveraged fund it finds. With a few thousand funds that takes several minutes (the board fills in as it goes). After that it refreshes every `REFRESH_MINUTES` and re-scans for new funds every `DISCOVERY_HOURS`. The SQLite file grows to a few hundred MB with the full global universe.
Your data lives in the `etfme-data` volume (SQLite), so upgrading or recreating the container doesn't lose your targets, alerts or forecasts.

**No internet or just looking around?** `DEMO_MODE=true docker compose up -d`

**Without Docker:**
```bash
pip install -r requirements.txt
uvicorn app.main:app --reload
```

## Deploying & upgrading

* **Image:** every push to `main` and every `v*` tag is tested, then built for `linux/amd64` and `linux/arm64` (Raspberry Pi and Apple Silicon included) and published to `ghcr.io/dannyspace21/etf-me-in-the-etn` as `latest`, `X.Y.Z`, `X.Y` and `sha-…`.
* **Manual upgrade:** `docker compose pull && docker compose up -d`
* **Automatic upgrade:** `docker compose --profile autoupdate up -d` starts Watchtower, which checks for a new image every 6 hours.
* **Pin a version:** `IMAGE=ghcr.io/dannyspace21/etf-me-in-the-etn:2.0.0 docker compose up -d`
* **Release:** `git tag v2.0.0 && git push --tags`
* The container runs as a non-root user, has a `/healthz` healthcheck and reads all its config from environment variables, so it also runs as-is on Fly.io, Railway, Render, Kubernetes, Unraid, Synology and similar.

## Finding every leveraged fund (discovery)

You no longer have to maintain the fund list by hand. On startup, and then every `DISCOVERY_HOURS` (default 12), the app pulls the symbol lists below, keeps every product whose name says it is leveraged (`3X`, `UltraPro`, `Daily (2x) Leveraged`, `-3x Short`, `MegaLong (3X)`, `LevDAX`, `Geared`, Korean `레버리지` / `인버스2X`, Taiwanese `正2`, Japanese `レバレッジ`, Hong Kong's coded `XL2CSOPHSTECH` …), works out leverage, bull/bear, issuer, underlying and group, and de-duplicates funds listed on several European venues. Recognising a leveraged fund from its name is rule-based, so the odd fund can be missed or put in the wrong group; pin or exclude it in `config/universe.yaml` (see below).

| Source | Key? | Market | What it is | Complete? |
|---|---|---|---|---|
| `nasdaqtrader` | no | 🇺🇸 US | Nasdaq's symbol directory of every US-listed security (Nasdaq, NYSE, NYSE Arca, Cboe BZX) | ✅ |
| `alpaca` | free account | 🇺🇸 US | Every US asset on Alpaca | ✅ |
| `tsx` | no | 🇨🇦 Canada | Toronto Stock Exchange's company directory | ✅ |
| `hkex` | no | 🇭🇰 Hong Kong | HKEX's official List of Securities, "Leveraged and Inverse" category | ✅ |
| `twse` / `tpex` | no | 🇹🇼 Taiwan | Taiwan Stock Exchange and Taipei Exchange (OTC) open data | ✅ |
| `krx` | no | 🇰🇷 Korea | Every Korean ETF, via Naver Finance's public ETF list | ✅ |
| `jpx` | no | 🇯🇵 Japan | JPX's table of leveraged / inverse products listed **this year**: catches new Japanese launches | new only |
| `yahoo` | no | 🌍 everywhere | Keyword search: the only keyless route to London, Xetra, Paris, Milan, Amsterdam, Swiss, ASX and older Tokyo listings. Yahoo rate-limits it, so results vary from run to run | ❌ best effort |
| `eodhd` | key | 🌍 everywhere | Full symbol lists of every exchange in `EODHD_EXCHANGES`. **The way to get complete European and Australian coverage.** | ✅ |
| `fmp` | key | 🌍 everywhere | FMP's global ETF list | ✅ |

Sources without a key are skipped, so the defaults work out of the box: complete coverage of the US, Canada, Hong Kong, Taiwan and Korea, Japanese launches, and whatever Yahoo search turns up elsewhere. Add an EODHD key for every European and Australian listing.

**What the defaults find** (no API keys, live run on 22 Nov 2026): **934 leveraged funds**: 727 🇺🇸 US, 73 🇨🇦 Canada, 61 🇰🇷 Korea, 27 🇭🇰 Hong Kong, 17 🇬🇧 UK, 13 🇯🇵 Japan, 8 🇹🇼 Taiwan and a handful in Germany, France, Italy, the Netherlands, Australia and Singapore. 756 bull and 178 bear; 508 single-stock, 131 sector, 110 international-index, 76 broad-market, 56 commodity, 27 crypto, 20 rates. Europe and Australia are the thin spots: there's no free official list for them, so they depend on Yahoo search (or an EODHD key). The curated list in `config/universe.yaml` keeps the big ones (e.g. `QQQ3.L`, `3USL.L`, `LQQ.PA`, `1570.T`) tracked regardless.

* **New listings:** each source's first scan is a baseline: those funds already existed, so they get no alert and no badge. After that, any fund a source hasn't seen before fires a `🆕 New leveraged fund` alert and push notification, gets a **NEW** badge for 30 days, and has its full price history pulled right away. Because scans run every `DISCOVERY_HOURS`, a launch is picked up within about that long of appearing in a source's listings. Funds that only Yahoo search finds can also be older funds the search hadn't surfaced before.
* **Closures:** a fund that has been missing from every complete source that used to list it for `DISCOVERY_DELIST_DAYS` (default 7) is marked delisted and leaves the board. A missed Yahoo search never delists anything.
* **Scope knobs:** `DISCOVERY_INCLUDE_INVERSE=false` for bulls only, `DISCOVERY_MIN_LEVERAGE=2` to drop 1.25x/1.5x funds, `DISCOVERY_COUNTRIES=US,GB,KR` to limit markets.
* **Shipped fund list:** a GitHub Actions workflow (`.github/workflows/discovery-snapshot.yml`) runs this same discovery every day and commits the result to `config/discovered.json`. The app loads that file on startup, so it tracks the full list from the first second, even before its own first scan or with no internet. Add `ALPACA_API_KEY_ID`, `ALPACA_API_SECRET_KEY`, `EODHD_API_KEY` or `FMP_API_KEY` as repository secrets and the daily run uses them too. The workflow refuses to save a list of fewer than 150 funds, so a broken source can't wipe it.
* `config/universe.yaml` holds the ~80 curated funds (always tracked, and the whole universe in demo mode). Entries there override what discovery guesses, and its `exclude:` list hides tickers for good.
* `POST /api/discovery/run` scans now; `GET /api/discovery` shows the last run, per-source errors and a breakdown by market, leverage and group; `GET /api/funds/new?days=30` lists recent launches.

## Data sources (financial APIs)

Set `DATA_PROVIDERS` to a comma-separated list. They are tried in order until one returns data, and each fund only goes to providers that cover its market.

| Provider | Key? | Markets | Env var |
|---|---|---|---|
| `alpaca` (default, used when keys are set) | free account | US | `ALPACA_API_KEY_ID`, `ALPACA_API_SECRET_KEY` |
| `yahoo` (default) | no | US + international | n/a |
| `stooq` (default) | no | US, UK, DE, JP, HK | n/a |
| `eodhd` (default, used when key is set) | key | US + international | `EODHD_API_KEY` |
| `tiingo` | free key | US | `TIINGO_API_KEY` |
| `polygon` | key | US | `POLYGON_API_KEY` |
| `fmp` (Financial Modeling Prep) | key | US + international | `FMP_API_KEY` |
| `alphavantage` | key | US | `ALPHAVANTAGE_API_KEY` |

Non-US tickers use Yahoo-style suffixes (`QQQ3.L`, `LQQ.PA`, `QQU.TO`, `7226.HK`, `1570.T`, `122630.KS`, `00631L.TW`, `GGUS.AX`), prices are shown in the fund's own currency, and each provider translates the symbol to its own format.

### Alpaca

1. Create a free account at [alpaca.markets](https://alpaca.markets) and generate API keys (paper-trading keys are fine; the app only reads data).
2. Put them in `.env`:
   ```bash
   ALPACA_API_KEY_ID=PK...
   ALPACA_API_SECRET_KEY=...
   ```
3. Restart. Alpaca then handles **discovery of every US asset** and the **daily price updates for all US funds, batched 100 symbols per request**, so thousands of funds refresh in a few calls.

Notes: Alpaca's daily history starts in 2016, so a fund's first full backfill still comes from Yahoo/Stooq/EODHD, and Alpaca keeps it current after that. The free plan can't query the most recent 15 minutes of consolidated (SIP) data; the app asks for SIP up to 16 minutes ago and falls back to the IEX feed if your plan doesn't include SIP (`ALPACA_DATA_FEED=sip|iex`). `ALPACA_PAPER=false` points asset lookups at the live trading API.

### Rate limits & retries

Every outbound request goes through one HTTP layer that caps concurrent requests per host (`PER_HOST_CONCURRENCY`, default 4) and retries timeouts, connection drops, `429` and `5xx` with exponential backoff, honouring `Retry-After` (`HTTP_RETRIES`, default 3). Client errors such as `403` are not retried, so for example an Alpaca plan without SIP access falls straight back to IEX.

### Reverse splits

Leveraged funds reverse-split constantly. Every refresh compares the overlap between stored and freshly fetched prices, and if they disagree by more than 2% the fund's whole history is refetched, so a 1:10 reverse split never shows up as a fake +900% day.

Most providers are about 20 lines in `app/providers/__init__.py`, so adding another is easy.

## Notifications

Set `NOTIFY_URLS` to Discord / Slack webhooks, ntfy topics or any URL that accepts a JSON POST (comma separated). You get a push when a fund moves to another alert level, hits one of your price targets, or newly lists.

With thousands of funds that is a lot of pushes, so:

* `NOTIFY_MIN_TIER=brown` only pushes level changes at 💩 Code Brown or worse (`chill`, `clench`, `shock`, `brown`, `allin`). Everything still shows in the in-app feed.
* ⭐ **Starred funds always push**, whatever `NOTIFY_MIN_TIER` says. Star them from the board or a fund's page.
* `NOTIFY_NEW_FUNDS=false` turns off new-listing pushes.
* A fund's first data load backfills its recent level changes into the feed without pushing them.

## AI agents

The app is built so AI agents can read its data and write forecasts back. Machine-readable docs are at **`/docs`** (Swagger) and **`/openapi.json`**.

| Endpoint | What it does |
|---|---|
| `GET /api/agent/manifest` | Tool list in function-calling format; point an agent here first |
| `GET /api/agent/board?min_drawdown=40` | Compact list of all funds, worst first |
| `GET /api/agent/context/{ticker}` | Everything about one fund: summary, ladder prices, crash episodes, scorecard, Monte Carlo, your targets |
| `POST /api/agent/forecasts` | Post a forecast (stance, target, probability, rationale); it shows up in the UI |
| `POST /api/agent/ask` | Ask the server's own LLM about a fund |

```bash
curl -X POST localhost:8000/api/agent/forecasts -H 'Content-Type: application/json' \
  -H "X-API-Key: $KEY" -d '{"agent":"my-bot","ticker":"TQQQ","stance":"accumulate",
  "horizon_days":252,"target_price":60,"probability":55,"rationale":"Down 50%+, history says…"}'
```

Other useful endpoints: `GET /api/status` (which data sources are connected, refresh health), `GET /api/discovery` and `GET /api/funds/new` (see above), `PUT`/`DELETE /api/watchlist/{ticker}` (star / unstar).

`/api/agent/board` takes `direction`, `country`, `group`, `min_lev`, `min_drawdown` and `limit`, so an agent can ask e.g. for "every 3x bull in Korea down 60%+".

Set `AGENT_API_KEYS=key1,key2` to require a key on POST/PUT/DELETE endpoints. Leaving it empty keeps them open, which is fine on a home network but not on the public internet.

### Running the model offline (the default)

"Ask the AI" defaults to **local models**, so nothing leaves your machine:

```bash
docker compose --profile ai up -d
docker compose exec ollama ollama pull llama3.1:8b
# .env:  LLM_PROVIDER=ollama
```

`LLM_PROVIDER=openai` works with **any OpenAI-compatible server**: LM Studio, vLLM, llama.cpp, LocalAI, or hosted ones like OpenAI, OpenRouter and Groq (set `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY`).
`LLM_PROVIDER=anthropic` uses Claude through the official SDK (`LLM_API_KEY`; the model defaults to `claude-opus-5-5`).

## How the numbers work

* **Peak / drawdown:** split-adjusted closes. **Returns** (since 2011, 1y/2y after a crash) use dividend-adjusted closes. (EODHD only offers a fully adjusted series, so funds priced from EODHD use it for both.)
* **Prices** are daily closes; during market hours the latest bar is today's price so far, as of the last refresh. International funds are in their local currency, so don't compare a ₩ price with a $ one.
* **Crash episodes:** a new episode starts when the fund falls X% below its running peak and ends when it closes at a new high. *"Buy at −50%"* is the first close below the line, which you could actually have bought. *"From bottom"* assumes you caught the exact low, which nobody does.
* **Monte Carlo:** a stationary block bootstrap (10-day blocks, 3,000 paths) of the fund's *own* daily log returns. Because it resamples the leveraged fund itself rather than "index × 3", decay, fees and fat tails come along automatically. It's a sanity check, not a crystal ball.

## Project layout

```
app/
  main.py          FastAPI app + REST API
  agents.py        agent API (manifest, context, forecasts, ask)
  discovery.py     finds every leveraged fund (Nasdaq Trader, Alpaca, TSX, HKEX, TWSE/TPEx, KRX, JPX, Yahoo, EODHD, FMP)
  classify.py      fund name -> leverage, bull/bear, issuer, underlying, group
  exchanges.py     ticker suffix <-> exchange, country, currency, provider symbol formats
  net.py           shared HTTP client: per-host rate limit + retries
  llm.py           Ollama / OpenAI-compatible / Anthropic client
  service.py       refresh + discovery loops, alert engine, notifications
  analytics.py     drawdowns, tiers, crash episodes, scorecard, Monte Carlo
  providers/       Alpaca (batched), Yahoo, Stooq, EODHD, Tiingo, Polygon, FMP, Alpha Vantage, demo
  store.py         SQLite: prices, discovered funds, alerts, targets, watchlist, forecasts
  static/          single-page UI (vanilla JS + SVG, no CDN), icon.svg
config/            universe.yaml (pinned funds + exclude list), alerts.yaml (ladder + seeded targets),
                   discovered.json (every known leveraged fund, refreshed daily by GitHub Actions)
tests/             pytest
scripts/screenshots.py       regenerates the README screenshots
scripts/snapshot_universe.py runs discovery and writes config/discovered.json (used by the daily workflow)
```

Run the tests with `pip install -r requirements-dev.txt && pytest`.
Regenerate the screenshots with `DEMO_MODE=1 uvicorn app.main:app & python scripts/screenshots.py`.

## Disclaimer

This is a hobby tool and **not financial advice**. Leveraged ETFs reset daily and lose value in sideways, choppy markets. They can and do get closed, and a −90% fund needs +900% to get back to even. ETNs are unsecured debt of the issuer and carry its credit risk. Size your positions accordingly. "All the money the world has seen" is a figure of speech.
