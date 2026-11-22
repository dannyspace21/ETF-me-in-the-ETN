"""Temporary: print what candidate listing endpoints actually return."""
import asyncio, io, sys, zipfile
sys.path.insert(0, ".")
from app import net
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36"}
URLS = {
  "yahoo_lookup": "https://query1.finance.yahoo.com/v1/finance/lookup?query=leveraged&type=etf&count=100&start=0&formatted=false",
  "yahoo_lookup_3x": "https://query2.finance.yahoo.com/v1/finance/lookup?query=3x&type=etf&count=100&start=100&formatted=false",
  "yahoo_search_gb": "https://query2.finance.yahoo.com/v1/finance/search?q=3x%20long&quotesCount=100&newsCount=0&region=GB&lang=en-GB",
  "tsx": "https://www.tsx.com/json/company-directory/search/tsx/%5E*",
  "hkex": "https://www.hkex.com.hk/eng/services/trading/securities/securitieslists/ListOfSecurities.xlsx",
  "twse": "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL",
  "naver": "https://finance.naver.com/api/sise/etfItemList.nhn",
  "jpx": "https://www.jpx.co.jp/english/equities/products/etfs/leveraged-inverse/index.html",
}
async def main():
    async with net.client(60) as c:
        for k, u in URLS.items():
            try:
                r = await c.get(u, headers=UA)
                body = r.content
                print(f"\n##### {k} HTTP {r.status_code} {r.headers.get('content-type')} {len(body)} bytes")
                if body[:2] == b"PK":
                    z = zipfile.ZipFile(io.BytesIO(body)); print(z.namelist()[:10])
                    ss = z.read("xl/sharedStrings.xml").decode()[:3000]; print(ss)
                else:
                    txt = body.decode(r.encoding or "utf-8", "replace")
                    print(txt[:2500])
                    for kw in ("Lever", "lever", "2x", "2X", "正2", "레버리지", "Bull"):
                        i = txt.find(kw)
                        if i > 0: print(f"--- around {kw!r}: ...{txt[max(0,i-400):i+400]}..."); break
            except Exception as e:
                print(f"\n##### {k} FAILED {type(e).__name__}: {e}")
asyncio.run(main())
