"""
active_searcher.py
------------------
Actively searches Google News RSS for each client in the pipeline.
Targets M&A, FDI, investments, acquisitions — the things NSE RSS misses entirely.

Tier 1 clients (22): searched every refresh
Tier 2 clients (~30): searched every refresh
Others: passive name-matching only

Each client gets up to 3 targeted queries:
  1. "{subsidiary} acquisition OR deal OR investment India"
  2. "{MNC parent} India acquisition OR investment OR expansion"
  3. "{subsidiary} merger OR stake OR JV"
"""

import re
import datetime
import requests
import feedparser
import streamlit as st

HDR = {"User-Agent": "Mozilla/5.0 (SCB-Dashboard)", "Accept": "application/xml, */*"}
LOOKBACK_DAYS  = 30
LOOKAHEAD_DAYS = 30

# ─── Action classifier ───────────────────────────────────────────────────────

ACTION_KEYWORDS = {
    "M&A":       ["acqui", "merger", "takeover", "buyout", "stake purchase",
                  "open offer", "demerger", "amalgamat", "definitive agreement",
                  "signs agreement", "completes acquisition", "strategic investment",
                  "stake sale", "sells stake", "offloads stake", "divests"],
    "FDI":       ["capital infusion", "equity infusion", "rights issue",
                  "preferential allotment", "qip", "fpo", "fresh capital",
                  "capital raise", "foreign investment", "invests in india",
                  "sets up plant", "set up plant", "establishes", "greenfield",
                  "expands india", "new facility", "new plant", "india expansion",
                  # hub / service centre patterns — covers modern GCC / service hub FDI
                  "global hub", "service hub", "digital hub", "new hub",
                  "hub in", "hubs in", "set up hub", "sets up hub",
                  "global capability centre", "gcc", "global service centre",
                  "business services hub", "capability hub",
                  "global delivery centre", "global business services"],
    "IPO":       ["ipo", "initial public offering", "lists on", "stock exchange listing",
                  "files drhp", "sebi approval for listing"],
    "Buyback":   ["buyback", "buy-back", "share repurchase"],
    "Strategic": ["joint venture", " jv ", "partnership", "licensing agreement",
                  "technology agreement", "royalty agreement", "strategic alliance",
                  "mou", "memorandum of understanding", "signs mou",
                  "collaboration agreement"],
}

def _classify(text: str) -> str:
    t = text.lower()
    for action, kws in ACTION_KEYWORDS.items():
        if any(k in t for k in kws):
            return action
    return "Other"

def _clean(name: str, stops: list) -> str:
    r = name
    for w in stops:
        r = re.sub(r'\b' + re.escape(w) + r'\b', '', r, flags=re.IGNORECASE)
    return re.sub(r'\s+', ' ', r).strip()

def _amount(text: str):
    for pat in [r"₹\s*([\d,]+(?:\.\d+)?)\s*(?:crore|cr|lakh)?",
                r"\$\s*([\d,]+(?:\.\d+)?)\s*(?:mn|million|bn|billion)?",
                r"rs\.?\s*([\d,]+(?:\.\d+)?)",
                r"([\d,]+(?:\.\d+)?)\s*per\s+share"]:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            try: return float(m.group(1).replace(",", ""))
            except: pass
    return None

def _foreign(text: str):
    for pat in [
        r"(?:to|by|with|from)\s+([A-Z][A-Za-z\s&]{3,35}(?:Ltd|Limited|Inc|Corp|AG|SA|PLC|Group|Holdings|Management|Capital|Partners))",
        r"([A-Z][A-Za-z\s]{2,25}(?:Asset Management|Capital Partners|Investments|Ventures))",
    ]:
        m = re.search(pat, text)
        if m:
            n = m.group(1).strip()
            if len(n) > 5: return n
    return None

def _parse_pub(entry) -> str:
    pub = entry.get("published", "")
    if pub:
        try:
            from email.utils import parsedate_to_datetime
            return parsedate_to_datetime(pub).date().isoformat()
        except Exception:
            pass
    return datetime.date.today().isoformat()

def _in_window(date_str: str) -> bool:
    try:
        d = datetime.date.fromisoformat(str(date_str)[:10])
        f = datetime.date.today() - datetime.timedelta(days=LOOKBACK_DAYS)
        t = datetime.date.today() + datetime.timedelta(days=LOOKAHEAD_DAYS)
        return f <= d <= t
    except Exception:
        return True

def _gnews(query: str) -> list:
    """Hit Google News RSS with a targeted query."""
    enc = query.replace(" ", "+").replace('"', '%22').replace("&", "%26")
    url = f"https://news.google.com/rss/search?q={enc}&hl=en-IN&gl=IN&ceid=IN:en"
    out = []
    try:
        resp = requests.get(url, headers=HDR, timeout=12)
        feed = feedparser.parse(resp.content)
        for e in feed.entries[:20]:
            title   = e.get("title", "")
            summary = e.get("summary", "")
            date_str = _parse_pub(e)
            if title and _in_window(date_str):
                out.append({
                    "title":   title,
                    "summary": summary[:300],
                    "date":    date_str,
                    "url":     e.get("link", ""),
                })
    except Exception:
        pass
    return out


def _search_one_client(rec: dict) -> list:
    """
    Pull news for a single client from multiple sources:

      Source A — yfinance Ticker.news (if NSE ticker known)
                 Direct Yahoo Finance news for the stock.
                 Most reliable for listed Indian subsidiaries — no query guessing.

      Source B — Google News restricted to Indian financial sites
                 Targets ET / Moneycontrol / BS / Mint specifically.
                 Catches news that appears on those sites but not in
                 generic Google News RSS top-20.

      Source C — M&A / deal keyword queries on Google News (fallback,
                 especially useful for unlisted subsidiaries with no ticker).
    """
    import yfinance as yf

    sub    = rec.get("indian_subsidiary", "") or ""
    grp    = rec.get("client_group", "") or ""
    ticker = rec.get("ticker")

    GEN_STOPS  = ["india","limited","ltd","pvt","private","of","the","and"]
    GRP_STOPS  = ["group","corp","corporation","holdings","plc","ag","sa","bv","inc"]

    sub_q = _clean(sub, GEN_STOPS)
    grp_q = _clean(grp, GEN_STOPS + GRP_STOPS)

    seen    = set()
    actions = []

    def _add(title, summary, date_str, url, source_label):
        """Deduplicate and append a normalised action dict. Skips dividends
        (NSE RSS is authoritative for those — Source D handles dividend news)."""
        key = title[:60].lower()
        if key in seen or not title:
            return
        if not _in_window(date_str):
            return
        seen.add(key)
        combined = title + " " + summary
        atype    = _classify(combined)
        if atype == "Dividend":
            return                        # NSE RSS handles dividends; Source D catches news
        actions.append({
            "company_name":   sub[:60],
            "ticker":         ticker,
            "action_type":    atype,
            "headline":       title[:200],
            "date":           date_str,
            "amount":         _amount(combined),
            "currency":       "INR" if ("₹" in combined or
                                        "crore" in combined.lower()) else "USD",
            "source":         source_label,
            "raw_detail":     summary[:300],
            "url":            url,
            "foreign_entity": _foreign(combined),
            "_pre_matched":   rec,
        })

    def _add_dividend(title, summary, date_str, url, source_label):
        """Deduplicate and append dividend items — does NOT skip dividend type."""
        key = title[:60].lower()
        if key in seen or not title:
            return
        if not _in_window(date_str):
            return
        seen.add(key)
        combined = title + " " + summary
        atype    = _classify(combined)
        # Only keep if genuinely dividend-related
        if atype not in ("Dividend", "Other"):
            atype = "Dividend"
        if not any(k in combined.lower() for k in
                   ["dividend","ex-date","ex date","record date","repatriat","payout"]):
            return
        actions.append({
            "company_name":   sub[:60],
            "ticker":         ticker,
            "action_type":    "Dividend",
            "headline":       title[:200],
            "date":           date_str,
            "amount":         _amount(combined),
            "currency":       "INR",
            "source":         source_label,
            "raw_detail":     summary[:300],
            "url":            url,
            "foreign_entity": None,
            "_pre_matched":   rec,
        })

    # ── Source A: yfinance ticker news ────────────────────────────────────────
    if ticker:
        try:
            from concurrent.futures import ThreadPoolExecutor as _TPE
            with _TPE(max_workers=1) as _ex:
                _f = _ex.submit(lambda: yf.Ticker(f"{ticker}.NS").news or [])
                news_items = _f.result(timeout=3)   # hard 3-second cap
            for item in news_items[:10]:
                ts       = item.get("providerPublishTime", 0)
                date_str = (datetime.date.fromtimestamp(ts).isoformat()
                            if ts else datetime.date.today().isoformat())
                content  = (item.get("content") or
                            item.get("summary") or
                            item.get("description") or "")
                _add(item.get("title",""), content, date_str,
                     item.get("link",""), "Yahoo Finance")
        except Exception as e:
            print(f"  yfinance news failed for {ticker}: {e}")

    # ── Source B: Google News restricted to Indian financial sites ─────────────
    INDIA_SITES = (
        "site:economictimes.indiatimes.com OR site:moneycontrol.com "
        "OR site:business-standard.com OR site:livemint.com "
        "OR site:timesofindia.indiatimes.com OR site:thehindu.com "
        "OR site:deccanchronicle.com OR site:financialexpress.com"
    )
    if len(sub_q) > 4:
        for item in _gnews(f'"{sub_q}" ({INDIA_SITES})'):
            _add(item["title"], item["summary"], item["date"], item["url"],
                 "News — India")
    if len(grp_q) > 4 and grp_q.lower() != sub_q.lower():
        for item in _gnews(f'"{grp_q}" India ({INDIA_SITES})'):
            _add(item["title"], item["summary"], item["date"], item["url"],
                 "News — India")

    # ── Source C: M&A / deal keyword queries (fallback) ───────────────────────
    kw_queries = []
    if len(sub_q) > 4:
        kw_queries.append(f'"{sub_q}" acquisition OR merger OR deal OR investment')
        kw_queries.append(f'"{sub_q}" stake OR IPO OR JV OR expansion')
        kw_queries.append(f'"{sub_q}" India')
    if len(grp_q) > 4:
        kw_queries.append(
            f'"{grp_q}" India acquisition OR investment OR expansion 2025 OR 2026')

    for q in kw_queries[:4]:
        for item in _gnews(q):
            _add(item["title"], item["summary"], item["date"], item["url"],
                 "Google News")

    # ── Source D: Dividend-specific queries ───────────────────────────────────
    # Separate from M&A queries — results are kept (not skipped) even if
    # classified as Dividend. Catches announcements that NSE RSS misses when
    # the ex-date is >30 days out or the feed was polled before announcement.
    if len(sub_q) > 4:
        div_query = f'"{sub_q}" dividend OR "ex-date" OR "record date" 2026'
        for item in _gnews(div_query):
            _add_dividend(item["title"], item["summary"],
                          item["date"], item["url"], "Google News — Dividend")

    return actions[:12]   # hard cap — keeps classifier token budget sustainable


@st.cache_data(ttl=1800)
def fetch_active_search(tier1_keys: tuple, tier2_keys: tuple,
                        tier3_keys: tuple, _snapshot: tuple) -> list:
    """
    Cached active search for Tier 1 + Tier 2 + top Tier 3 clients.
    Runs per-client fetches in parallel (ThreadPoolExecutor) so total
    wall-clock time ≈ slowest single client, not sum of all clients.
    _snapshot is used only as a cache key.
    """
    from client_registry import load_registry
    from concurrent.futures import ThreadPoolExecutor, as_completed

    try:
        reg = load_registry()
    except Exception:
        return []

    by_key = {
        (r.get("client_group",""), r.get("indian_subsidiary","")): r
        for r in reg["all"]
    }

    all_keys = list(tier1_keys) + list(tier2_keys[:50]) + list(tier3_keys)
    records  = [by_key[k] for k in all_keys if k in by_key]

    all_actions = []

    # 12-way parallelism — keeps total wall time to ~single-client latency
    with ThreadPoolExecutor(max_workers=12) as pool:
        futures = {pool.submit(_search_one_client, rec): rec for rec in records}
        for fut in as_completed(futures, timeout=90):
            try:
                all_actions += fut.result(timeout=8)
            except Exception:
                pass   # one slow/failed client never blocks the rest

    print(f"  Active search: {len(records)} clients → {len(all_actions)} items")
    return all_actions


def get_search_keys(registry: dict):
    """Return hashable keys for Tier 1 + Tier 2 clients, plus top Tier 3 by NIH."""
    tier1 = tuple(
        (r.get("client_group",""), r.get("indian_subsidiary",""))
        for r in registry["all"]
        if "TIER 1" in (r.get("priority_tier",""))
    )
    tier2 = tuple(
        (r.get("client_group",""), r.get("indian_subsidiary",""))
        for r in registry["all"]
        if "TIER 2" in (r.get("priority_tier",""))
    )
    # Top 20 Tier 3 / untiered companies by NIH exposure — ensures high-value
    # Tier 3 (e.g. BASF when it was miscategorised) get RSS scanned without
    # flooding the classifier with too many items
    tier3_top = tuple(
        (r.get("client_group",""), r.get("indian_subsidiary",""))
        for r in sorted(
            [r for r in registry["all"]
             if "TIER 1" not in (r.get("priority_tier","") or "").upper()
             and "TIER 2" not in (r.get("priority_tier","") or "").upper()
             and r.get("indian_subsidiary")],
            key=lambda r: r.get("net_nih_exposure", 0) or 0,
            reverse=True
        )[:20]
    )
    snap = tuple(r.get("indian_subsidiary","")[:15] for r in registry["all"][:10])
    return tier1, tier2, tier3_top, snap
