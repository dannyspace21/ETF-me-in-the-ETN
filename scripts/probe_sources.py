"""Temporary: inspect public exchange listing files (round 2)."""
import asyncio, io, re, sys, zipfile
import httpx
UA = {"User-Agent": "etf-me-in-the-etn/2.1 (+https://github.com/dannyspace21/ETF-me-in-the-ETN)"}

def cells(row):
    return [re.sub(r"<[^>]+>", "", m) for m in re.findall(r"<(?:x:)?c\b[^>]*>(.*?)</(?:x:)?c>", row, re.S)]

async def main():
    async with httpx.AsyncClient(timeout=60, follow_redirects=True, headers=UA) as c:
        try:
            r = await c.get("https://www.hkex.com.hk/eng/services/trading/securities/securitieslists/ListOfSecurities.xlsx")
            z = zipfile.ZipFile(io.BytesIO(r.content)); sh = z.read("xl/worksheets/sheet1.xml").decode()
            rows = re.findall(r"<(?:x:)?row[^>]*>(.*?)</(?:x:)?row>", sh, re.S)
            print(f"##### hkex rows={len(rows)}")
            for row in rows[:5]: print(cells(row))
            n = 0
            for row in rows:
                cs = cells(row)
                if any("Leveraged" in x or "Inverse" in x for x in cs):
                    if n < 30: print(cs)
                    n += 1
            print("hkex L&I rows:", n)
        except Exception as e:
            print("hkex FAILED", type(e).__name__, e)
        try:
            r = await c.get("https://www.jpx.co.jp/english/equities/products/etfs/leveraged-inverse/index.html")
            t = r.text; i = t.find("<table"); print(f"\n##### jpx tables={t.count('<table')}"); print(re.sub(r"\s+", " ", t[i:i+4000]))
            print("jpx file links:", sorted(set(re.findall(r'href="([^"]+\.(?:xlsx?|csv))"', t)))[:20])
        except Exception as e:
            print("jpx FAILED", type(e).__name__, e)
        for k, u in {"tpex_etf": "https://www.tpex.org.tw/openapi/v1/tpex_etf_daily_close_quotes",
                     "tpex": "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes"}.items():
            try:
                r = await c.get(u); print(f"\n##### {k} HTTP {r.status_code} {len(r.content)}"); print(r.text[:500])
                m = re.search(r".{200}正2.{200}", r.text); print("around 正2:", m.group(0) if m else None)
            except Exception as e:
                print(k, "FAILED", type(e).__name__, e)
        try:
            r = await c.get("https://www.tsx.com/json/company-directory/search/tsx/%5E*")
            j = r.json()["results"]; lev = [x for x in j if re.search(r"(?i)\b\d(\.\d+)?x\b|leveraged|bull|bear", x["name"])]
            print(f"\n##### tsx {len(j)} listings, {len(lev)} look leveraged"); print([(x["symbol"], x["name"]) for x in lev[:40]])
        except Exception as e:
            print("tsx FAILED", type(e).__name__, e)
asyncio.run(main())
