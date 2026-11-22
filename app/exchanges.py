"""Exchange metadata, keyed by the Yahoo-style ticker suffix the app uses as its
canonical symbol (``TQQQ`` = US, ``QQQ3.L`` = London, ``1570.T`` = Tokyo …).

Every international provider speaks a slightly different dialect, so this is the
one place that maps between them."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Exchange:
    suffix: str        # Yahoo suffix ("" = US)
    name: str
    country: str       # ISO-3166 alpha-2
    currency: str
    eodhd: str | None  # EODHD exchange code
    stooq: str | None  # Stooq suffix


EXCHANGES: dict[str, Exchange] = {e.suffix: e for e in [
    Exchange("", "US (NYSE / Nasdaq / Cboe)", "US", "USD", "US", "us"),
    Exchange("L", "London", "GB", "GBP", "LSE", "uk"),
    Exchange("DE", "Xetra", "DE", "EUR", "XETRA", "de"),
    Exchange("F", "Frankfurt", "DE", "EUR", "F", None),
    Exchange("PA", "Euronext Paris", "FR", "EUR", "PA", None),
    Exchange("AS", "Euronext Amsterdam", "NL", "EUR", "AS", None),
    Exchange("BR", "Euronext Brussels", "BE", "EUR", "BR", None),
    Exchange("MI", "Borsa Italiana", "IT", "EUR", "MI", None),
    Exchange("MC", "Madrid", "ES", "EUR", "MC", None),
    Exchange("SW", "SIX Swiss", "CH", "CHF", "SW", None),
    Exchange("ST", "Stockholm", "SE", "SEK", "ST", None),
    Exchange("OL", "Oslo", "NO", "NOK", "OL", None),
    Exchange("CO", "Copenhagen", "DK", "DKK", "CO", None),
    Exchange("HE", "Helsinki", "FI", "EUR", "HE", None),
    Exchange("VI", "Vienna", "AT", "EUR", "VI", None),
    Exchange("TO", "Toronto", "CA", "CAD", "TO", None),
    Exchange("NE", "Cboe Canada", "CA", "CAD", "NEO", None),
    Exchange("HK", "Hong Kong", "HK", "HKD", "HK", "hk"),
    Exchange("T", "Tokyo", "JP", "JPY", "TSE", "jp"),
    Exchange("KS", "Korea (KOSPI)", "KR", "KRW", "KO", None),
    Exchange("KQ", "Korea (KOSDAQ)", "KR", "KRW", "KQ", None),
    Exchange("TW", "Taiwan", "TW", "TWD", "TW", None),
    Exchange("TWO", "Taipei Exchange", "TW", "TWD", "TWO", None),
    Exchange("AX", "ASX", "AU", "AUD", "AU", None),
    Exchange("SI", "Singapore", "SG", "SGD", "SG", None),
    Exchange("SS", "Shanghai", "CN", "CNY", "SHG", None),
    Exchange("SZ", "Shenzhen", "CN", "CNY", "SHE", None),
    Exchange("NS", "NSE India", "IN", "INR", "NSE", None),
    Exchange("TA", "Tel Aviv", "IL", "ILS", "TA", None),
    Exchange("JO", "Johannesburg", "ZA", "ZAR", "JSE", None),
    Exchange("SA", "B3 Brazil", "BR", "BRL", "SA", None),
]}

# Venues that mostly carry *cross-listings* of funds whose home market we already
# cover (Mexico's SIC lists US ETFs, German regional exchanges list Xetra ETPs …).
CROSS_LISTING_SUFFIXES = {"MX", "SN", "BA", "DU", "MU", "BE", "SG", "HM", "HA", "VI", "F", "IL"}

# When the same European ETP is listed on several venues, keep the first of these.
VENUE_PRIORITY = ["", "L", "DE", "PA", "AS", "MI", "SW", "BR", "MC", "ST", "TO", "NE", "AX", "HK",
                  "T", "KS", "KQ", "TW", "TWO", "SI", "SS", "SZ", "NS", "TA", "JO", "SA", "OL", "CO", "HE"]

EODHD_TO_SUFFIX = {e.eodhd: e.suffix for e in EXCHANGES.values() if e.eodhd}
# Yahoo's own exchange codes (as returned by its search API) -> suffix
YAHOO_EXCH_TO_SUFFIX = {
    "NMS": "", "NGM": "", "NCM": "", "NYQ": "", "PCX": "", "ASE": "", "BTS": "", "NAS": "", "NYS": "", "PNK": None,
    "LSE": "L", "IOB": "L", "GER": "DE", "FRA": "F", "PAR": "PA", "AMS": "AS", "BRU": "BR", "MIL": "MI",
    "MCE": "MC", "EBS": "SW", "SWX": "SW", "STO": "ST", "OSL": "OL", "CPH": "CO", "HEL": "HE", "VIE": "VI",
    "TOR": "TO", "NEO": "NE", "HKG": "HK", "JPX": "T", "TYO": "T", "KSC": "KS", "KOE": "KQ", "TAI": "TW",
    "TWO": "TWO", "ASX": "AX", "SES": "SI", "SHH": "SS", "SHZ": "SZ", "NSI": "NS", "TLV": "TA", "JNB": "JO",
    "SAO": "SA",
}


def split(ticker: str) -> tuple[str, str]:
    """'QQQ3.L' -> ('QQQ3', 'L'); 'TQQQ' -> ('TQQQ', ''); 'BRK.B' style US classes stay US."""
    if "." in ticker:
        base, suf = ticker.rsplit(".", 1)
        if suf.upper() in EXCHANGES or suf.upper() in CROSS_LISTING_SUFFIXES:
            return base, suf.upper()
    return ticker, ""


def exchange_of(ticker: str) -> Exchange:
    return EXCHANGES.get(split(ticker)[1], EXCHANGES[""])


def is_us(ticker: str) -> bool:
    return split(ticker)[1] == ""


def venue_rank(ticker: str) -> int:
    suf = split(ticker)[1]
    return VENUE_PRIORITY.index(suf) if suf in VENUE_PRIORITY else len(VENUE_PRIORITY)
