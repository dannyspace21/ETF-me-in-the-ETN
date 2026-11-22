"""Turns a fund's *name* into structured metadata.

Listing feeds (Nasdaq Trader, Alpaca, EODHD, Yahoo …) only give a symbol and a
name, so this is how the app recognises a leveraged ETF/ETN/ETP and works out
its leverage, direction, issuer, underlying and group. It is heuristic but
covers every naming style issuers actually use, e.g.

  ProShares UltraPro QQQ                              -> 3x long
  Direxion Daily Semiconductor Bear 3X Shares         -> 3x short
  MicroSectors FANG+ Index 3X Leveraged ETNs          -> 3x long ETN
  GraniteShares 2x Long NVDA Daily ETF                -> 2x long, single-stock NVDA
  Amundi Nasdaq-100 Daily (2x) Leveraged UCITS ETF    -> 2x long
  Leverage Shares -3x Short Tesla ETP                 -> 3x short ETP
  NEXT FUNDS Nikkei 225 Leveraged Index ETF           -> 2x long (Japan)
  KODEX 200 Futures Inverse 2X                        -> 2x short (Korea)
  Xtrackers LevDAX Daily Swap UCITS ETF               -> 2x long
  BetaShares Geared US Equity Fund                    -> 2x long (Australia)
"""
from __future__ import annotations

import re
from dataclasses import dataclass

MIN_LEV, MAX_LEV = 1.2, 10.0

ISSUERS = [
    ("ProShares", r"\bproshares\b"), ("Direxion", r"\bdirexion\b"), ("MicroSectors", r"\bmicrosectors\b"),
    ("GraniteShares", r"\bgraniteshares\b"), ("T-Rex", r"\bt-?rex\b|\brex shares\b"), ("Defiance", r"\bdefiance\b"),
    ("Tradr", r"\btradr\b"), ("Leverage Shares", r"\bleverage ?shares\b"), ("Volatility Shares", r"\bvolatility shares\b"),
    ("AXS", r"\baxs\b"), ("Roundhill", r"\broundhill\b"), ("Global X", r"\bglobal ?x\b|\bhorizons\b|\bbetapro\b"),
    ("WisdomTree", r"\bwisdomtree\b|\bboost\b"), ("Amundi", r"\bamundi\b|\blyxor\b"), ("Xtrackers", r"\bxtrackers\b"),
    ("iShares", r"\bishares\b"), ("CSOP", r"\bcsop\b"), ("Samsung", r"\bsamsung\b|\bkodex\b"),
    ("Mirae Asset", r"\bmirae\b|\btiger\b"), ("KB", r"\bkbstar\b|\brise\b"), ("Hanwha", r"\barirang\b|\bplus\b"),
    ("Nomura", r"\bnext funds\b|\bnomura\b"), ("Daiwa", r"\bifree\b|\bdaiwa\b"), ("Simplex", r"\bsimplex\b"),
    ("Mitsubishi UFJ", r"\bmaxis\b"), ("Yuanta", r"\byuanta\b"), ("Fubon", r"\bfubon\b"), ("Cathay", r"\bcathay\b"),
    ("BetaShares", r"\bbetashares\b"), ("UBS ETRACS", r"\betracs\b"), ("iPath", r"\bipath\b"),
    ("VelocityShares", r"\bvelocityshares\b"), ("Valkyrie", r"\bvalkyrie\b"), ("21Shares", r"\b21shares\b"),
    ("Invesco", r"\binvesco\b"), ("Leverage Shares", r"\blvl\b"), ("Societe Generale", r"\bsociete generale\b"),
    ("Vontobel", r"\bvontobel\b"), ("Hang Seng Investment", r"\bhang seng investment\b"), ("Premia", r"\bpremia\b"),
    ("ChinaAMC", r"\bchinaamc\b"), ("Mirae Asset", r"\bglobal x japan\b"), ("Kraneshares", r"\bkraneshares\b"),
    ("Bitwise", r"\bbitwise\b"), ("Teucrium", r"\bteucrium\b"), ("Cboe", r"\bcboe\b"),
]

# Words that make "Ultra"/"Leveraged" mean something else.
FALSE_POSITIVES = re.compile(
    r"ultra[- ]?short[- ](term|duration|bond|income|muni)|ultra[- ]short (term|duration|income|bond)"
    r"|leveraged loan|loan|buyout|senior|floating|low[- ]volatility|min(imum)? vol|covered call"
    r"|buffer|defined outcome|option income|yieldmax|premium income|\b1x\b|x[- ]?ray|\bmatrix\b", re.I)

SHORT_RE = re.compile(r"\b(short(?![- ]term)|inverse|bear|ultrashort|double inverse|bär)\b|\(-\d", re.I)

# word -> group
GROUP_WORDS = [
    ("crypto", r"bitcoin|\bbtc\b|ether(eum)?\b|\beth\b|solana|\bsol\b|\bxrp\b|crypto|dogecoin|\bdoge\b|litecoin|cardano|avalanche|\bsui\b|chainlink"),
    ("volatility", r"\bvix\b|volatility"),
    ("currency", r"\bdollar\b|\beuro\b(?! ?stoxx)|\byen\b|\byuan\b|\brenminbi\b|\bfx\b|currency|sterling|\bgbp\b"),
    ("rates", r"treasury|\bbond|\byr\b|\byear\b|\btips\b|\b20\+|\b7-10\b|gilt|\bbund\b|duration|\bjgb\b"),
    ("commodity", r"\bgold|silver|crude|\boil\b|\bwti\b|brent|natural gas|nat gas|copper|platinum|palladium|commodit|"
                  r"metals?|miners|uranium|wheat|\bcorn\b|soybean|agricultur|lithium"),
    ("intl", r"china|chinese|japan|nikkei|topix|korea|kospi|kosdaq|india|nifty|brazil|mexico|europe|euro ?stoxx|"
             r"emerging|\beafe\b|developed|hang seng|hscei|\bhstech\b|\bhsi\b|taiwan|\btaiex\b|\bdax\b|ftse ?100|"
             r"\bftse mib\b|cac ?40|\bsmi\b|\bibex\b|\bomx\b|australia|asx ?200|canada|\btsx\b|latin|asia|"
             r"vietnam|israel|south africa|\buk\b|germany|france|italy|spain|csi ?300|a50|star 50|chinext|msci world"),
    ("broad", r"s&p ?500|s&p500|\bspx\b|nasdaq|\bqqq\b|\bndx\b|dow jones industrial|\bdow ?30\b|\bdow\b(?! jones u)|russell|"
              r"mid ?cap|small ?cap|\bus 500\b|\bus 100\b|\bus tech 100\b|total (us )?market|\bsp ?500\b|"
              r"s&p ?400|s&p ?600|high beta|equal weight|\bus equity|\bus large"),
]

# Common single stocks, spelled out (matched case-insensitively) ...
COMPANY_NAMES = (
    "tesla|nvidia|apple|amazon|microsoft|alphabet|google|meta platforms|facebook|netflix|coinbase|microstrategy|"
    "strategy inc|palantir|advanced micro|broadcom|intel|taiwan semiconductor|tsmc|alibaba|baidu|"
    "berkshire|jpmorgan|goldman sachs|boeing|exxon|eli lilly|novo nordisk|unitedhealth|walmart|costco|salesforce|"
    "oracle|adobe|uber|airbnb|shopify|snowflake|crowdstrike|super ?micro|arm holdings|robinhood|marathon digital|"
    "riot platforms|hims|rivian|lucid|gamestop|sofi|rocket lab|ionq|rigetti|d-wave|quantum computing inc|oklo|"
    "vistra|constellation energy|asml|samsung electronics|sk hynix|toyota|sony|softbank|nintendo|lvmh|ferrari|"
    "siemens|rheinmetall|astrazeneca|hsbc|barclays|rolls-royce|tencent|xiaomi|meituan|byd|li auto|xpeng|"
    "circle internet|galaxy digital|bitmine|sharplink|reddit|duolingo|applovin|carvana|affirm|upstart|cloudflare|"
    "datadog|mongodb|roblox|draftkings|spotify|paypal|qualcomm|micron|lam research|applied materials|marvell|"
    "dell technologies|cisco|ibm|nio inc|pinduoduo|mercadolibre|moderna|pfizer|nike|disney|starbucks|ford|"
    "general motors|caterpillar|lockheed|nebius|coreweave|tempus|joby|archer aviation|serve robotics"
)
# ... and as tickers (matched only as an all-caps token)
COMPANY_TICKERS = set("""TSLA NVDA AAPL AMZN MSFT GOOGL GOOG META NFLX COIN MSTR PLTR AVGO SMCI HOOD MU INTC BABA BIDU LLY
NVO UNH WMT COST CRM ORCL ADBE SHOP SNOW CRWD MARA RIOT RIVN LCID GME SOFI RKLB RGTI QBTS QUBT OKLO VST CEG ASML TSM
CRCL GLXY BMNR SBET RDDT APP CVNA AFRM UPST NET DDOG MDB RBLX DKNG SPOT PYPL XYZ QCOM AMAT MRVL AMD ARM UBER ABNB
IONQ HIMS NIO PDD MELI MRNA PFE NKE DIS SBUX F GM BA XOM JPM GS BRKB NBIS CRWV TEM JOBY ACHR SERV SMR CCJ""".split())

SECTOR_WORDS = (r"semiconductor|technology|tech\b|financ|bank|biotech|health|pharma|real estate|reit|homebuild|"
                r"retail|transport|utilit|consumer|internet|software|cloud|cyber|energy|oil & gas|industrial|"
                r"aerospace|defen[cs]e|materials|fang|magnificent|mag ?7|innovation|ai\b|artificial intelligence|"
                r"robotic|clean|solar|cannabis|airline|travel|insurance|telecom|media|gaming|esports|quantum|"
                r"nuclear|infrastructure|dividend|growth|value|momentum|sector|industry")

STRIP_WORDS = re.compile(
    r"\b(direxion shares etf trust|proshares trust ii|etf trust|trust ii?|daily|bull|bear|long|short|inverse|"
    r"leveraged?|ultra ?pro|ultra ?short|ultra|shares?|etfs?|etns?|etps?|etcs?|index|fund|target|strategy|"
    r"ucits|product|securities|swap|acc|dist|class [a-z]|exchange traded (notes?|funds?|products?)|notes?|"
    r"geared|hedge fund|common|units?|plus|the|series|tracker|traded|currency hedged|hedged|usd|eur|gbp|"
    r"futures|daily target|leverage shares|graniteshares|direxion|proshares|microsectors|defiance|tradr|"
    r"t-rex|rex|volatility shares|axs|wisdomtree|amundi|lyxor|xtrackers|global x|betapro|horizons|csop|"
    r"kodex|tiger|kbstar|arirang|next funds|ifree|maxis|yuanta|fubon|cathay|betashares|etracs|ipath|"
    r"velocityshares|invesco|ishares|21shares|valkyrie|bitwise|roundhill|samsung|mirae asset|boost|"
    r"double|triple|bmo|ubs ag|monthly pay|monthly|reset|lev|ii)\b", re.I)


@dataclass
class Classified:
    lev: float
    direction: str   # "long" | "short"
    type: str        # ETF | ETN | ETP
    issuer: str
    under: str
    group: str


def _num(s: str) -> float | None:
    try:
        v = float(s)
    except ValueError:
        return None
    return v if MIN_LEV <= v <= MAX_LEV else None


def leverage(name: str) -> tuple[float | None, bool]:
    """(leverage, short?) or (None, _) if the name doesn't describe a leveraged product."""
    n = name.lower()
    if FALSE_POSITIVES.search(n):
        return None, False
    short = bool(SHORT_RE.search(n))
    # explicit multipliers: 3x, 2X, (2x), -3x, 1.5x, 2xLeveraged, x2, x3
    for m in re.finditer(r"(?<![\w.])[-–]?(\d{1,2}(?:\.\d{1,2})?)\s?x(?=$|[^a-z]|lev|long|short|bull|bear|inv|daily)", n):
        v = _num(m.group(1))
        if v:
            return v, short or m.group(0).lstrip().startswith(("-", "–"))
    m = re.search(r"(?<![\w.])x\s?(\d(?:\.\d)?)\b", n)
    if m and _num(m.group(1)):
        return _num(m.group(1)), short
    # ProShares Ultra family
    if "proshares" in n or re.search(r"\bultrapro\b", n):
        if re.search(r"\bultrapro\b", n):
            return 3.0, short
        if re.search(r"\bultrashort\b", n):
            return 2.0, True
        if re.search(r"\bultra\b", n):
            return 2.0, short
    if re.search(r"\bultra long\b", n):          # Global X Australia
        return 2.0, False
    if re.search(r"\btriple\b", n):
        return 3.0, short
    if re.search(r"\bdouble (inverse|short|long)\b|\bdouble\b.*\b(bull|bear)\b", n):
        return 2.0, short
    if re.search(r"\blevdax\b|\blev ?dax\b", n):
        return 2.0, False
    if re.search(r"\bgeared\b", n):              # BetaShares
        return 2.0, short
    # Asia: "KODEX Leverage", "NEXT FUNDS Nikkei 225 Leveraged Index" are 2x
    stripped = re.sub(r"\bleverage ?shares\b", "", n)
    if re.search(r"\bleverage(d)?\b", stripped) and not re.search(r"\bun-?leveraged\b|\bdeleverag", stripped):
        return 2.0, short
    return None, False


def classify(name: str, *, symbol: str = "", hint_type: str | None = None, country: str = "US") -> Classified | None:
    if not name:
        return None
    if " - " in name:  # "Direxion Shares ETF Trust - Direxion Daily ... Bull 3X Shares"
        name = name.split(" - ", 1)[1] if len(name.split(" - ", 1)[1]) > 8 else name
    lev, short = leverage(name)
    if lev is None:
        return None
    n = name.lower()
    if re.search(r"\betns?\b|exchange traded notes?|\bnotes?\b", n):
        typ = "ETN"
    elif re.search(r"\betps?\b|\betcs?\b|\bsecurities\b|\bleverage shares\b|\bboost\b", n) and "etf" not in n:
        typ = "ETP"
    else:
        typ = (hint_type or "ETF").upper() if (hint_type or "ETF").upper() in {"ETF", "ETN", "ETP"} else "ETF"
    issuer = next((label for label, rx in ISSUERS if re.search(rx, n)), name.split()[0] if name.split() else "")
    return Classified(lev=lev, direction="short" if short else "long", type=typ, issuer=issuer,
                      under=underlying(name), group=group_of(name, symbol, country))


def underlying(name: str) -> str:
    s = re.sub(r"\(?[-–]?\d{1,2}(\.\d{1,2})?\s?x\)?(?=$|[^a-z]|lev)", " ", name, flags=re.I)
    s = re.sub(r"\(?currency[- ]hedged\)?|\b(short|mid|long)[- ]term\b", " ", s, flags=re.I)
    s = re.sub(r"\bx\s?\d\b", " ", s, flags=re.I)
    s = STRIP_WORDS.sub(" ", s)
    s = re.sub(r"[(),]|\s-\s|\s+-$", " ", s)
    s = re.sub(r"\s+", " ", s).strip(" -–&/")
    return s or name


def group_of(name: str, symbol: str = "", country: str = "US") -> str:
    n = re.sub(r"currency[- ]hedged", "", name.lower())
    for g, rx in GROUP_WORDS[:5]:  # crypto, volatility, currency, rates, commodity
        if re.search(rx, n):
            return g
    under = underlying(name)
    toks = under.split()
    if not re.search(r"semiconductor|sector|select|industry|internet|fang|magnificent|mag ?7|index", n) and (
            (len(toks) == 1 and toks[0] in COMPANY_TICKERS)
            or (len(toks) == 1 and re.fullmatch(r"[A-Z]{2,5}", toks[0]) and re.search(r"\b(long|short|bull|bear)\b", n)
                and toks[0] not in {"QQQ", "SPX", "NDX", "DAX", "HSI", "SPY", "DOW", "MSCI", "FTSE", "CSI", "VIX"})
            or re.search(rf"\b({COMPANY_NAMES})\b", under.lower())):
        return "single-stock"
    intl_rx, broad_rx = GROUP_WORDS[5][1], GROUP_WORDS[6][1]
    if re.search(intl_rx, n):
        return "intl"
    if re.search(SECTOR_WORDS, n):
        return "sector"
    if re.search(broad_rx, n):
        return "broad"
    return "intl" if country not in ("", "US") else "sector"
