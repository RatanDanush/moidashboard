"""
filters.py
----------
Centralised keyword filters shared across corporate_fetcher.py
and client_registry.py.

Keeps filter logic in one place so updates propagate everywhere.
"""

# ─── Market commentary / equity noise ────────────────────────────────────────
# Headlines matching these are NOT corporate actions

MARKET_COMMENTARY_PHRASES = [
    # Share price updates
    "share price live", "stock price live", "live update", "live updates",
    "price performance", "performance snapshot", "market behavior",
    "share price today", "stock price today", "trading session",
    # Analyst ratings
    "rated buy", "rated sell", "rated hold", "rates buy", "rates sell",
    "price target", "initiates coverage", "upgrades to", "downgrades to",
    "outperform", "underperform", "neutral rating", "buy rating",
    "sell rating", "hold rating", "target price",
    # Post-IPO stock performance
    "trades below issue price", "trades above issue price",
    "below ipo price", "above ipo price", "ipo performance",
    "how india's biggest ipos", "biggest ipos performing",
    "stock falls", "stock rises", "shares fall", "shares rise",
    "shares tumble", "shares surge", "shares drop",
    "stock surges", "stock jumps", "shares jump",
    "hits 52-week", "52-week high", "52-week low", "all-time high",
    # General market commentary
    "wall street gains", "wall street falls", "market roundup",
    "weekly wrap", "market wrap", "earnings beat", "earnings miss",
    "revenue miss", "quarterly results", "q1 results", "q2 results",
    "q3 results", "q4 results", "annual results",
    "stocks pull back", "markets pull back", "stocks retreat",
    "japanese stocks", "us stocks", "asian stocks", "global stocks",
    # Technical analysis
    "support level", "resistance level", "rsi", "moving average",
    "market cap",
    # ── Stock pick / investment list articles ──────────────────────────────────
    "stocks to invest", "stocks to buy", "stocks to watch",
    "high-priced stocks", "best stocks", "top stocks",
    "stocks in india in", "stocks for", "stocks to consider",
    "shares to buy", "shares to watch",
    # ── Smart investing / retail investor content ──────────────────────────────
    "what you need to know for smart investing",
    "smart investing", "stock: what you need to know",
    "invest in india in april", "invest in india in",
    "investing now", "for smart investing",
    # ── Executive appointments / HR news ──────────────────────────────────────
    "extends tenure", "extend his tenure", "extend her tenure",
    "cfo extends", "ceo extends", "md extends", "director extends",
    "appoints cfo", "appoints ceo", "appoints md", "names new cfo",
    "names new ceo", "new managing director", "new chief financial",
    "new chief executive", "joins as cfo", "joins as ceo",
    "promoted to cfo", "promoted to ceo",
    "annual report", "fy2025 annual report", "fy2026 annual report",
    "strong performance marks milestone",
    "raises prices on select plans", "cuts benefits and raises prices",
    "new plans rolled out", "plan prices", "price hike on plans",
    "service tariff", "prepaid plans", "postpaid plans",
    "broadband prices", "data plan",
    # ── Legal / court cases (Bug #9) ──────────────────────────────────────────
    "court acquits", "court convicts", "court dismisses",
    "bail granted", "arrested for", "death case",
    "criminal case", "criminal charges", "filed fir",
    "pedestrian death", "hit and run",
    # ── Sports / entertainment name collisions (Bug #25) ──────────────────────
    "big bash", "bbl ", "test cricket", "ipl match",
    "cricket match", "odi cricket", "t20 cricket",
    "fires back at rumours", "fires back at reports",
    "fires back at claims",
    # ── Government bonds / macro stats — not corporate actions (Bug #26) ──────
    "dated securities", "government securities sale",
    "g-sec auction", "treasury bills auction", "gsec auction",
    "sale of two dated", "notified amount of",
    # ── State / macro FDI statistics (Bug #23) ────────────────────────────────
    "fdi flows into", "fdi inflows into", "fdi received by",
    "fdi in the state", "crore fdi flows",
    # ── Personal finance articles (Bug #5) ────────────────────────────────────
    "ppf extension", "ppf withdrawal", "ppf account maturity",
    "public provident fund", "fixed deposit maturity",
    "mutual fund sip", "what should investors choose",
    "account maturity", "phased withdrawals",
]

# ─── Aggregator / list articles ───────────────────────────────────────────────
AGGREGATOR_PHRASES = [
    "list of ", "top acquisitions", "acquisitions by ",
    "tracxn", "crunchbase", "cb insights",
    "deal tracker", "m&a tracker", "acquisition tracker",
    "funding rounds", "latest funding",
    # ── ET / news site tag / aggregator pages (Bug #1, #29) ───────────────────
    "latest news & videos", "latest news and videos",
    "photos about", "news videos photos",
    "latest news, videos", "videos, photos about",
    # ── Market research databases (Bug #29) ───────────────────────────────────
    "indexbox", "news and statistics -", "- news and statistics",
    "market research report", "industry report",
    "- statista", "mordor intelligence", "grand view research",
]

# ─── Secondary reference patterns ────────────────────────────────────────────
# Article is primarily about another company — client is only mentioned
SECONDARY_REFERENCE_TEMPLATES = [
    "ceo joins", "cfo joins", "executive joins", "director joins",
    "adds {name} ceo", "hires {name}", "appoints {name} ceo",
    "ex-{name}", "former {name} ceo", "former {name} executive",
    "{name} ceo joins", "{name} executive joins",
    "{name} board member joins", "board of {name}",
]

# ─── Commonly confused company pairs ─────────────────────────────────────────
# Never cross-match these even if name fragments overlap
NEVER_MATCH = {
    "basf":           ["bayer", "bayercrop", "bayer cropscience"],
    "bayer":          ["basf"],
    "abbvie":         ["abbott", "abbotindia"],
    "abbott":         ["abbvie"],
    "siemens energy": ["siemens"],
    "siemens":        ["siemens energy"],
    "linde":          ["lindeindia"],
    "pfizer":         ["piramal"],
    "glaxo":          ["haleon"],
    "shell":          [],
    "unilever":       [],
    "novartis":       [],
}

# ─── Words stripped from names before matching ───────────────────────────────
NAME_STOP_WORDS = {
    "ltd", "pvt", "limited", "india", "private", "of", "the", "and",
    "group", "corp", "corporation", "holdings", "plc", "ag", "sa",
    "bv", "inc", "llc", "gmbh", "nv",
}

# ─── Action types worth triggering Groq web search ───────────────────────────
# If RSS finds these types for a client, escalate to Groq web search
WEB_SEARCH_TRIGGER_TYPES = {"M&A", "FDI", "Strategic", "IPO", "Buyback", "Restructuring"}

# ─── Helpers ─────────────────────────────────────────────────────────────────

def is_market_commentary(headline: str) -> bool:
    h = headline.lower()
    if any(p in h for p in MARKET_COMMENTARY_PHRASES):
        return True
    if any(p in h for p in AGGREGATOR_PHRASES):
        return True
    return False

def is_object_in_stake_action(headline: str, client_name: str) -> bool:
    """
    Returns True if the matched company is the OBJECT of a stake action,
    not the subject — i.e. someone else is acting on the company's shares.

    Keep  (subject): "Siemens cuts stake in Innomotics"
    Filter (object): "Goldman Sachs cuts Siemens Energy stake"

    Safety rule: if company name appears BEFORE the verb, it's the actor → keep.
    Safety rule: if both company and a parent/related name appear, default keep.
    """
    import re
    h    = headline.lower()
    name = client_name.lower()
    for w in ["group", "corporation", "holdings", "plc", "ag", "sa", "inc",
              "ltd", "limited", "india", "pvt", "private"]:
        name = re.sub(r'\b' + re.escape(w) + r'\b', '', name).strip()
    name = re.sub(r'\s+', ' ', name).strip()
    if not name or len(name) < 4:
        return False
    if name not in h:
        return False

    STAKE_VERBS = (
        r"cuts?|reduces?|trims?|sells?|offloads?|divests?|exits?|"
        r"raises?|increases?|lifts?|boosts?|buys?|acquires?|adds?"
    )
    # Pattern: [anything] [verb] [company] [stake/shares/holding/interest]
    # This means company is the object
    obj_pattern = re.compile(
        rf".+?\b({STAKE_VERBS})\b.{{0,40}}{re.escape(name)}.{{0,30}}"
        rf"\b(stake|shares?|holding|interest|position|equity)\b",
        re.IGNORECASE,
    )
    # Pattern: [company] [verb] — company is the subject
    subj_pattern = re.compile(
        rf"^{re.escape(name)}\b.{{0,60}}\b({STAKE_VERBS})\b",
        re.IGNORECASE,
    )

    if subj_pattern.match(h):
        return False   # company is the actor — keep

    if obj_pattern.search(h):
        # Safety: if company name appears before the first stake verb, it's
        # probably the subject in a different clause — default keep
        first_verb_match = re.search(rf"\b({STAKE_VERBS})\b", h)
        if first_verb_match:
            name_pos = h.find(name)
            verb_pos = first_verb_match.start()
            if name_pos < verb_pos:
                return False  # name before verb → subject → keep
        return True   # company is the object → filter

    return False


def is_secondary_reference(headline: str, client_name: str) -> bool:
    """True if client appears as secondary reference in the headline."""
    h    = headline.lower()
    name = client_name.lower()
    # Strip generic words for cleaner matching
    for w in ["group", "corporation", "holdings", "plc", "ag", "sa", "inc"]:
        name = name.replace(w, "").strip()
    if not name or len(name) < 4:
        return False
    patterns = [
        f"adds {name} ceo", f"hires {name}", f"appoints {name}",
        f"{name} ceo joins", f"{name} executive joins",
        f"former {name}", f"ex-{name}",
        f"{name} board member", f"board of ",
    ]
    if any(p in h for p in patterns):
        return True
    # Stake-action object detection
    if is_object_in_stake_action(headline, client_name):
        return True
    return False

def pre_filter(headline: str) -> bool:
    """
    Fast keyword pre-filter before sending to classifier.
    Returns True if headline should be KEPT (not filtered).
    Returns False if headline is obvious noise.

    Dividend headlines are always kept — even if packaged with earnings
    results coverage (e.g. "Q4 results: board declares ₹720 dividend").
    """
    h = headline.lower()
    # Always keep dividend / ex-date / buyback headlines regardless of other phrases
    ALWAYS_KEEP = [
        "dividend", "ex-date", "ex date", "record date",
        "repatriat", "buyback", "buy-back", "share repurchase",
        "open offer", "sebi open offer",
    ]
    if any(k in h for k in ALWAYS_KEEP):
        return True
    return not is_market_commentary(headline)


# ─── Non-India geography override ────────────────────────────────────────────
# The 8b model sometimes marks inr_involved=True for investments in France,
# Germany, Ohio etc. These deterministic checks override that.

NON_INDIA_GEOS = [
    # "in X" phrases
    "in france", "in germany", "in the us", "in the usa", "in the united states",
    "in ohio", "in alabama", "in texas", "in california", "in michigan",
    "in the uk", "in united kingdom", "in china", "in japan", "in south korea",
    "in australia", "in canada", "in mexico", "in brazil",
    "in europe", "in pakistan", "in bangladesh", "in vietnam", "in indonesia",
    "in thailand", "in malaysia", "in saudi arabia", "in the uae", "in dubai",
    "in the netherlands", "in netherlands", "in belgium", "in spain",
    "in italy", "in poland", "in sweden", "in norway", "in finland",
    "in madagascar", "in africa", "in sri lanka", "in myanmar",
    "in singapore", "in hong kong", "in taiwan",
    # bare country names (safe because INVESTMENT_KEYWORDS guard is required)
    "netherlands", "germany", "france", "united states", "united kingdom",
    "australia", "canada", "brazil", "south korea", "saudi arabia",
    "malaysia", "madagascar", "sri lanka",
    # adjective forms
    "european facility", "european plant", "european factory",
    "french facility", "french plant", "french factory",
    "german facility", "german plant", "german factory", "germany factory",
    "american facility", "american plant",
    "malaysian ", "madagascar ",
]

INVESTMENT_KEYWORDS = [
    "invest", "investment", "facility", "plant", "factory",
    "expansion", "expands", "manufactur", "production",
    "greenfield", "new site", "new center", "new centre",
    "refiner", "data cent", "set up", "establishes", "warehouse",
]

def is_non_india_geography_investment(headline: str) -> bool:
    """
    Returns True (should force inr_involved=False) if the headline describes
    an investment / facility in a non-India location with no India mention.

    Catches: "Cargill announces major investment ... facility in France"
    Safe:    any headline that mentions India / Indian.
    """
    h = headline.lower()
    if "india" in h or "indian" in h:
        return False
    if not any(kw in h for kw in INVESTMENT_KEYWORDS):
        return False
    return any(geo in h for geo in NON_INDIA_GEOS)


# ─── IPO subject mismatch detector ───────────────────────────────────────────
# Catches: "AI Boom Update: Cerebras IPO, Apple vs Big Tech AI Spend"
# The 8b model sees "Apple" + "IPO" and attributes the IPO to Apple India.

def is_ipo_mismatch(headline: str, client_name: str) -> bool:
    """
    Returns True if 'X IPO' appears in the headline where X is a capitalised
    company name clearly different from the matched client.

    Safe:    "Apple India files for IPO", "Apple IPO approval from SEBI"
    Catches: "Cerebras IPO, Apple vs Big Tech AI Spend" (client = Apple India)
    """
    import re
    h = headline.lower()
    if "ipo" not in h:
        return False

    # Find capitalised company name(s) immediately before "IPO"
    named_ipos = re.findall(
        r'([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+){0,3})\s+IPO\b',
        headline
    )
    if not named_ipos:
        return False   # no "X IPO" pattern — don't filter

    # Build client core words (4+ chars, strip generic stops)
    name = client_name.lower()
    for stop in ["india", "pvt", "ltd", "limited", "private",
                 "group", "inc", "corp", "corporation"]:
        name = name.replace(stop, "").strip()
    name = re.sub(r'\s+', ' ', name).strip()
    client_words = {w for w in re.split(r'\W+', name) if len(w) >= 4}
    if not client_words:
        return False

    # If ANY named IPO subject matches a client word → client IS the IPO entity
    for subject in named_ipos:
        subj_lower = subject.lower()
        if any(cw in subj_lower for cw in client_words):
            return False   # client is the IPO entity — keep

    return True   # all named IPO subjects are different companies → mismatch
