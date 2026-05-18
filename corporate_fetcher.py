"""
corporate_fetcher.py  —  v2
---------------------------
Fetches corporate actions from 4 sources.
Window: past 30 days + upcoming 30 days (captures both history and ex-dates).

Sources (in priority order):
  1. NSE RSS  — no key needed, India-specific, real-time
  2. yfinance — no key needed, dividend/split history
  3. FMP      — needs free key, dividend calendar + M&A feed
  4. Finnhub  — needs free key, company news with action detection
"""

import os, re, json, datetime, requests, feedparser
import streamlit as st
import yfinance as yf
from dotenv import load_dotenv

load_dotenv()

FINNHUB_KEY    = os.getenv("FINNHUB_API_KEY", "")
FMP_KEY        = os.getenv("FMP_API_KEY", "")
LOOKBACK_DAYS  = 30
LOOKAHEAD_DAYS = 30

# ─── Date helpers ─────────────────────────────────────────────────────────────

def _today():   return datetime.date.today()
def _from_dt(): return _today() - datetime.timedelta(days=LOOKBACK_DAYS)
def _to_dt():   return _today() + datetime.timedelta(days=LOOKAHEAD_DAYS)
def _fmt(d):    return d.strftime("%Y-%m-%d")

def _in_window(date_str: str) -> bool:
    try:
        d = datetime.date.fromisoformat(str(date_str)[:10])
        return _from_dt() <= d <= _to_dt()
    except Exception:
        return True


# ─── Action classification ────────────────────────────────────────────────────

KEYWORDS = {
    "Dividend":   ["dividend", "interim dividend", "final dividend",
                   "special dividend", "repatriat", "payout", "ex-date",
                   "ex date", "record date", "distribution"],
    "M&A":        ["acqui", "merger", "takeover", "buyout", "stake purchase",
                   "open offer", "delisting", "demerger", "amalgamat",
                   "scheme of arrangement", "strategic stake"],
    "FDI":        ["fdi", "foreign direct investment", "capital infusion",
                   "equity infusion", "rights issue", "preferential allotment",
                   "qip", "fpo", "fresh capital", "capital raise"],
    "Buyback":    ["buyback", "buy-back", "share repurchase"],
    "Strategic":  ["joint venture", " jv ", "partnership", "licensing",
                   "technology agreement", "royalty", "strategic alliance",
                   "collaboration", "mou"],
    "Stock Split": ["stock split", "bonus share", "sub-division", "split"],
}

def classify(text: str) -> str:
    t = text.lower()
    for action, kws in KEYWORDS.items():
        if any(k in t for k in kws):
            return action
    return "Other"


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _company(title: str) -> str:
    return re.split(r"[-:|/]", title)[0].strip()[:80]

import html as _html

def _sanitize_headline(text: str) -> str:
    """Strip HTML tags and unescape entities from headlines (Bug #22)."""
    text = _html.unescape(text or "")
    text = re.sub(r'<[^>]+>', '', text)   # strip <span>, <b>, etc.
    text = re.sub(r'\s+', ' ', text).strip()
    return text

def _amount(text: str):
    """
    Unit-aware financial amount extraction.

    Priority:
      1. Explicit per-share patterns  (e.g. "₹22 per share", "Rs 21 dividend")
         → rejected if value > 5,000  (avoids crore-scale numbers as /sh)
      2. Deal values with unit multipliers:
         • "$X billion / bn"  → X × 1,000 (stored as USD millions)
         • "$X million / mn"  → X  (stored as USD millions)
         • "₹X crore"         → X  (stored as INR crore, displayed separately)
      3. Raw ₹ / INR amount only if ≤ 5,000

    Fixes:
      Bug #3  — "$1 billion" → 1000, not 1
      Bug #3  — "$36 bn"     → 36000, not 36
      Bug #4  — Hyundai profit ₹1,256 crore rejected as /sh (> 5,000 cap)
      Bug #26 — ₹32,000 crore G-sec rejected as /sh (> 5,000 cap)
      Bug #21 — ₹90 crore startup raise rejected as /sh (crore path instead)
    """
    h = text.lower()

    # ── Tier 1: Explicit per-share / dividend amounts ──────────────────────────
    PER_SHARE_PATS = [
        r'rs\.?\s*([\d,]+(?:\.\d+)?)\s*(?:per\s+share|/\s*share|per\s+equity\s+share)',
        r'([\d,]+(?:\.\d+)?)\s*(?:per\s+share|/\s*share)',
        r'dividend\s+(?:of\s+)?(?:rs\.?\s*|₹\s*)([\d,]+(?:\.\d+)?)',
        r'(?:rs\.?\s*|₹\s*)([\d,]+(?:\.\d+)?)\s*(?:dividend\b|per\s+share|/sh\b)',
        r'₹\s*([\d,]+(?:\.\d+)?)\s*(?:per\s+share|/sh\b)',
        r'inr\s*([\d,]+(?:\.\d+)?)\s*(?:per\s+share|/sh\b)',
    ]
    for pat in PER_SHARE_PATS:
        m = re.search(pat, h, re.IGNORECASE)
        if m:
            try:
                val = float(m.group(1).replace(",", ""))
                if val <= 5_000:      # sanity cap: reject crore-scale as /sh
                    return val
            except Exception:
                pass

    # ── Tier 2: Deal values with unit multipliers ──────────────────────────────
    DEAL_PATS = [
        (r'\$\s*([\d,]+(?:\.\d+)?)\s*(?:trillion|tn\b)',                    1_000_000),
        (r'\$\s*([\d,]+(?:\.\d+)?)\s*(?:billion|bn\b)',                     1_000),
        (r'usd\s*([\d,]+(?:\.\d+)?)\s*(?:billion|bn\b)',                    1_000),
        (r'([\d,]+(?:\.\d+)?)\s*(?:billion|bn\b)\s*(?:dollar|usd|\$)',      1_000),
        (r'\$\s*([\d,]+(?:\.\d+)?)\s*(?:million|mn\b|m\b)',                 1),
        (r'usd\s*([\d,]+(?:\.\d+)?)\s*(?:million|mn\b)',                    1),
        (r'([\d,]+(?:\.\d+)?)\s*(?:million|mn\b)\s*(?:dollar|usd|\$)',      1),
        # INR crore — returned as raw crore value (app.py handles display)
        (r'(?:rs\.?\s*|₹\s*)([\d,]+(?:\.\d+)?)\s*(?:lakh\s+crore)',        10_000),
        (r'([\d,]+(?:\.\d+)?)\s*(?:lakh\s+crore)',                          10_000),
        (r'(?:rs\.?\s*|₹\s*)([\d,]+(?:\.\d+)?)\s*(?:crore|cr\b)',          None),
        (r'([\d,]+(?:\.\d+)?)\s*(?:crore|cr\b)',                            None),
    ]
    for pat, multiplier in DEAL_PATS:
        m = re.search(pat, h, re.IGNORECASE)
        if m:
            try:
                val = float(m.group(1).replace(",", ""))
                if multiplier is not None:
                    return round(val * multiplier, 1)
                else:
                    return val   # crore returned as-is
            except Exception:
                pass

    # ── Tier 3: Raw ₹ / INR — only if small (likely per-share) ───────────────
    for pat in [r'₹\s*([\d,]+(?:\.\d+)?)', r'inr\s*([\d,]+(?:\.\d+)?)']:
        m = re.search(pat, h, re.IGNORECASE)
        if m:
            try:
                val = float(m.group(1).replace(",", ""))
                if val <= 5_000:
                    return val
            except Exception:
                pass

    return None

def _ticker(text: str, known: set):
    for t in known:
        if re.search(r'\b' + re.escape(t) + r'\b', text.upper()):
            return t
    return None

def _foreign(text: str):
    for pat in [r"\bto\s+([A-Z][A-Za-z &]{3,35}(?:Ltd|Limited|Inc|Corp|AG|SA|PLC|Group|Holdings))",
                r"\bby\s+([A-Z][A-Za-z &]{3,35}(?:Ltd|Limited|Inc|Corp|AG|SA|PLC|Group|Holdings))"]:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            n = m.group(1).strip()
            if len(n) > 4: return n
    return None

def _parse_date(raw: str) -> str:
    raw = raw.strip().replace(" ", "-")
    for fmt in ["%d-%b-%Y", "%d-%B-%Y", "%d/%b/%Y", "%d-%m-%Y", "%Y-%m-%d"]:
        try: return datetime.datetime.strptime(raw, fmt).date().isoformat()
        except: pass
    return _fmt(_today())

def _pub_date(entry) -> str:
    pub = entry.get("published", "")
    if pub:
        try:
            from email.utils import parsedate_to_datetime
            return parsedate_to_datetime(pub).date().isoformat()
        except: pass
    return _fmt(_today())


# ─── SOURCE 1: NSE RSS ────────────────────────────────────────────────────────

NSE_FEEDS = [
    ("Corporate Actions",  "https://nsearchives.nseindia.com/content/RSS/Corporate_action.xml"),
    ("Board Meetings",     "https://nsearchives.nseindia.com/content/RSS/BoardMeeting.xml"),
    ("Announcements",      "https://nsearchives.nseindia.com/content/RSS/Announcement.xml"),
    ("Corp Announcements", "https://nsearchives.nseindia.com/content/RSS/CorporateAnnouncement.xml"),
]
HDR = {"User-Agent": "Mozilla/5.0 (SCB-Dashboard)", "Accept": "application/xml, */*"}

def fetch_nse(known_tickers: set) -> list:
    out = []
    for name, url in NSE_FEEDS:
        try:
            resp = requests.get(url, headers=HDR, timeout=15)
            feed = feedparser.parse(resp.content)
            n = 0
            for e in feed.entries:
                title   = e.get("title", "")
                summary = e.get("summary", "")
                combined = title + " " + summary

                # Use ex-date from title if present, else published date
                date_str = _pub_date(e)
                m = re.search(
                    r"ex[-\s]?date[:\s]+(\d{1,2}[-\s/]\w{3,9}[-\s/]\d{4}|\d{4}-\d{2}-\d{2})",
                    combined, re.IGNORECASE
                )
                if m:
                    date_str = _parse_date(m.group(1))

                if not _in_window(date_str):
                    continue

                out.append({
                    "company_name":   _company(title),
                    "ticker":         _ticker(title, known_tickers),
                    "action_type":    classify(combined),
                    "headline":       _sanitize_headline(title)[:200],
                    "date":           date_str,
                    "amount":         _amount(combined),
                    "currency":       "INR",
                    "source":         f"NSE — {name}",
                    "raw_detail":     summary[:300],
                    "url":            e.get("link", ""),
                    "foreign_entity": None,
                })
                n += 1
            print(f"  NSE [{name}]: {n} items in window (feed has {len(feed.entries)} total)")
        except Exception as ex:
            print(f"  NSE [{name}] error: {ex}")
    return out


# ─── SOURCE 2: yfinance ───────────────────────────────────────────────────────

@st.cache_data(ttl=3600)   # cache 1 hour — prevents repeated calls on each Streamlit re-run
def fetch_yfinance(tickers: tuple) -> list:
    """
    Fetch dividend + split history for all NSE-listed tickers.
    Takes a tuple (not list) so st.cache_data can hash the argument.
    Called from fetch_all_corporate_actions as: fetch_yfinance(tuple(all_tickers))
    """
    out, n = [], 0
    wide_from = _today() - datetime.timedelta(days=730)  # 2 years

    for ticker in tickers:
        try:
            t = yf.Ticker(f"{ticker}.NS")
            # history(actions=True) is most reliable for Indian NSE stocks
            hist = t.history(period="2y", actions=True)
            if hist is None or hist.empty:
                continue
            for col, atype in [("Dividends", "Dividend"), ("Stock Splits", "Stock Split")]:
                if col not in hist.columns:
                    continue
                series = hist[col][hist[col] > 0]
                for dt_idx, val in series.items():
                    try:
                        d = dt_idx.date() if hasattr(dt_idx, "date") else datetime.date.fromisoformat(str(dt_idx)[:10])
                    except Exception:
                        continue
                    if d < wide_from:
                        continue
                    label = f"₹{val:.2f}/share" if atype == "Dividend" else f"{val}:1"
                    out.append({
                        "company_name": ticker, "ticker": ticker,
                        "action_type": atype,
                        "headline": f"{ticker} — {atype.lower()} {label}",
                        "date": d.isoformat(), "amount": float(val),
                        "currency": "INR" if atype == "Dividend" else None,
                        "source": "yfinance",
                        "raw_detail": "Yahoo Finance dividend/split history",
                        "url": f"https://finance.yahoo.com/quote/{ticker}.NS",
                        "foreign_entity": None,
                    })
                    n += 1
        except Exception:
            pass
    print(f"  yfinance: {n} actions across {len(tickers)} tickers (last 2 years)")
    return out


# ─── SOURCE 3: FMP ────────────────────────────────────────────────────────────

def fetch_fmp(tickers: list) -> list:
    out = []
    if not FMP_KEY:
        print("  FMP: skipped — add FMP_API_KEY to .env for dividend calendar + M&A")
        return out
    ticker_set = {t.upper() for t in tickers}
    try:
        url = (f"https://financialmodelingprep.com/api/v3/stock_dividend_calendar"
               f"?from={_fmt(_from_dt())}&to={_fmt(_to_dt())}&apikey={FMP_KEY}")
        data = requests.get(url, timeout=15).json()
        n = 0
        for item in (data if isinstance(data, list) else []):
            sym = item.get("symbol", "").replace(".NS", "").upper()
            if sym not in ticker_set: continue
            date_str = str(item.get("date", _fmt(_today())))[:10]
            if not _in_window(date_str): continue
            out.append({
                "company_name": sym, "ticker": sym, "action_type": "Dividend",
                "headline": f"{sym} — dividend ₹{item.get('dividend','?')} | ex-date {date_str}",
                "date": date_str, "amount": item.get("dividend"),
                "currency": "INR", "source": "FMP",
                "raw_detail": str(item)[:200], "url": "",
                "foreign_entity": None,
            })
            n += 1
        print(f"  FMP dividends: {n} matched tickers")
    except Exception as ex:
        print(f"  FMP error: {ex}")
    try:
        url = (f"https://financialmodelingprep.com/api/v4/mergers-acquisitions-rss-feed"
               f"?page=0&apikey={FMP_KEY}")
        data = requests.get(url, timeout=15).json()
        n = 0
        for item in (data if isinstance(data, list) else [])[:50]:
            title  = item.get("title", "")
            detail = item.get("description", "")
            date   = str(item.get("date", _fmt(_today())))[:10]
            if not _in_window(date): continue
            if "india" not in (title + detail).lower(): continue
            out.append({
                "company_name": _company(title), "ticker": None, "action_type": "M&A",
                "headline": _sanitize_headline(title)[:200], "date": date, "amount": _amount(title + detail),
                "currency": "USD", "source": "FMP M&A",
                "raw_detail": detail[:300], "url": item.get("url", ""),
                "foreign_entity": _foreign(title),
            })
            n += 1
        print(f"  FMP M&A: {n} India-related deals")
    except Exception as ex:
        print(f"  FMP M&A error: {ex}")
    return out


# ─── SOURCE 4: Indian Financial News RSS ─────────────────────────────────────
# Replaces Finnhub — much better Indian corporate coverage, no API key needed

INDIA_NEWS_FEEDS = [
    ("Economic Times Markets",    "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms"),
    ("Economic Times Companies",  "https://economictimes.indiatimes.com/markets/stocks/rssfeeds/2146842.cms"),
    ("Business Standard Markets", "https://www.business-standard.com/rss/markets-106.rss"),
    ("Business Standard Corps",   "https://www.business-standard.com/rss/companies-101.rss"),
    ("Moneycontrol Corporate",    "https://www.moneycontrol.com/rss/corporatenews.xml"),
    ("Mint Markets",              "https://www.livemint.com/rss/markets"),
]

def fetch_india_news(registry: dict) -> list:
    """Scrape Indian financial news, match to SCB clients by company name."""
    from client_registry import match_by_name
    out, n = [], 0
    # Build name fragments for fast matching
    fragments = []
    for rec in registry["all"]:
        sub = rec.get("indian_subsidiary", "") or ""
        clean = re.sub(r"\b(india|limited|ltd|pvt|private|of|the)\b", "",
                       sub.lower(), flags=re.IGNORECASE).strip()
        if len(clean) > 5:
            fragments.append((clean, rec))

    for feed_name, url in INDIA_NEWS_FEEDS:
        try:
            resp = requests.get(url, headers=HDR, timeout=12)
            feed = feedparser.parse(resp.content)
            fc = 0
            for e in feed.entries[:60]:
                title   = e.get("title", "")
                summary = e.get("summary", "")
                link    = e.get("link", "")
                combined = title + " " + summary
                date_str = _pub_date(e)
                if not _in_window(date_str):
                    continue
                atype = classify(combined)
                # Match to SCB client
                matched = None
                cl = combined.lower()
                for frag, rec in fragments:
                    if frag in cl:
                        matched = rec
                        break
                # Only include if matches SCB client OR strong action signal
                if not matched and atype in ("Other",):
                    continue
                item = {
                    "company_name": _company(title),
                    "ticker":       matched["ticker"] if matched else None,
                    "action_type":  atype if atype != "Other" else "Strategic",
                    "headline":     _sanitize_headline(title)[:200],
                    "date":         date_str,
                    "amount":       _amount(combined),
                    "currency":     "INR",
                    "source":       f"News — {feed_name}",
                    "raw_detail":   summary[:300],
                    "url":          link,
                    "foreign_entity": _foreign(combined),
                }
                if matched:
                    item["_pre_matched"] = matched
                out.append(item)
                fc += 1; n += 1
            print(f"  News [{feed_name}]: {fc} relevant items (feed: {len(feed.entries)} total)")
        except Exception as ex:
            print(f"  News [{feed_name}] error: {ex}")
    print(f"  Indian news RSS total: {n} items")
    return out


def fetch_finnhub(tickers: list) -> list:
    """Kept for compatibility — replaced by Indian news RSS."""
    if FINNHUB_KEY:
        print("  Finnhub: has key but skipped — poor Indian NSE coverage; using Indian news RSS")
    return []


@st.cache_data(ttl=1800, show_spinner=False,
               hash_funcs={dict: lambda d: len(d.get("all_clients", d))})
def fetch_all_corporate_actions(registry: dict) -> tuple:
    """
    Returns (actions_list, signal_clients_set)
    signal_clients_set: client keys where RSS/GNews found M&A/FDI/Strategic/IPO
    for batch_manager to prioritise Groq web search.
    """
    from client_registry import match_by_name
    from filters import (pre_filter, WEB_SEARCH_TRIGGER_TYPES,
                         is_non_india_geography_investment, is_ipo_mismatch)
    all_tickers = [t for t in registry["by_ticker"].keys() if t]  # skip None tickers
    print(f"\n{'='*60}")
    print(f"Fetching | window {_fmt(_from_dt())} → {_fmt(_to_dt())}")
    print(f"Tickers: {len(all_tickers)} total")
    print(f"{'='*60}")

    # Structured sources
    raw  = fetch_nse(set(all_tickers))
    raw += fetch_yfinance(tuple(all_tickers))
    raw += fetch_fmp(all_tickers)
    raw += fetch_india_news(registry)

    # Active Google News search
    try:
        from active_searcher import fetch_active_search, get_search_keys
        t1, t2, t3, snap = get_search_keys(registry)
        active = fetch_active_search(t1, t2, t3, snap)
        raw += active
        print(f"  Active search: {len(active)} items")
    except Exception as ex:
        print(f"  Active search error: {ex}")

    # Batch cache (Groq web search results)
    try:
        from batch_manager import load_cache, get_all_cached_events
        cache        = load_cache()
        cached_items = get_all_cached_events(registry, cache)
        raw += cached_items
    except Exception as ex:
        print(f"  Batch cache error: {ex}")

    # Step 1b: Hard dividend cutoff — drop past dividends older than 90 days
    # Keep: upcoming dividends (future ex-dates) and dividends within 90 days
    cutoff_90 = datetime.date.today() - datetime.timedelta(days=90)
    div_before = len(raw)
    def _keep_dividend(a):
        if a.get("action_type") not in ("Dividend", "Stock Split"):
            return True
        try:
            d = datetime.date.fromisoformat(str(a.get("date",""))[:10])
            return d >= cutoff_90   # keep upcoming (future) and recent
        except Exception:
            return True
    raw = [a for a in raw if _keep_dividend(a)]
    print(f"  Dividend cutoff: removed {div_before - len(raw)} old dividend/split items")

    total_scanned = len(raw)

    # Step 2: Keyword pre-filter (free, zero tokens)
    pre_len = len(raw)
    raw     = [a for a in raw if pre_filter(a.get("headline",""))]
    print(f"  Pre-filter removed {pre_len - len(raw)} noise items")

    # Step 3: Classify — Groq primary (no RPM cost), Gemini as emergency fallback only.
    # Gemini's 15 RPM is reserved entirely for grounded web search in batch_manager.
    # Events with _gemini_verified=True are already classified by 2.5 Flash — skip them
    # to avoid wasting Groq tokens and overriding better Gemini judgement with 8b.
    to_classify = [(i, a) for i, a in enumerate(raw)
                   if not a.get("_gemini_verified")
                   and any(s in a.get("source","") for s in
                          ["Google News","News —","Moneycontrol",
                           "Economic","Business Standard","Mint",
                           "Google News — Dividend"])]
    if to_classify:
        hl_tuple = tuple(
            (a["headline"], a.get("raw_detail","")[:120])
            for _, a in to_classify
        )
        clfs = None

        # Primary: Groq llama-3.1-8b — fast, no RPM limit
        try:
            from groq_engine import batch_classify, GROQ_API_KEY
            from token_tracker import can_afford
            if GROQ_API_KEY and can_afford("classify", 500):
                print(f"  Groq classifying {len(to_classify)} news items...")
                clfs = batch_classify(hl_tuple)
            else:
                print("  Groq classify: budget insufficient, trying Gemini fallback")
        except Exception as ex:
            print(f"  Groq classifier error: {ex} — trying Gemini fallback")

        # Fallback: Gemini 3.1 Flash Lite (only when Groq is unavailable/exhausted)
        if clfs is None:
            try:
                from gemini_engine import gemini_classify, GEMINI_API_KEY
                if GEMINI_API_KEY:
                    print(f"  Gemini classifying {len(to_classify)} items (Groq fallback)...")
                    clfs = gemini_classify(hl_tuple)
            except Exception as ex:
                print(f"  Gemini classify fallback error: {ex}")

        if clfs:
            for (idx, action), clf in zip(to_classify, clfs):
                raw[idx]["_groq_confidence"]    = clf.get("confidence","medium")
                raw[idx]["_groq_significant"]   = clf.get("is_significant", True)
                raw[idx]["_inr_involved"]       = clf.get("inr_involved", True)
                raw[idx]["_skip_india_india"]   = clf.get("skip_india_india", False)
                raw[idx]["_indian_sub_div"]     = clf.get("is_indian_subsidiary_dividend", True)
                raw[idx]["_is_primary_subject"] = clf.get("is_primary_subject", True)
                raw[idx]["_sebi_open_offer"]    = clf.get("sebi_open_offer_trigger", False)
                ev_date = clf.get("event_date")
                if ev_date:
                    raw[idx]["_event_date"] = str(ev_date)[:10]
                if clf.get("confidence") in ("high","medium"):
                    raw[idx]["action_type"] = clf.get("action_type", action["action_type"])
                    raw[idx]["foreign_entity"] = (clf.get("foreign_entity") or
                                                  action.get("foreign_entity"))
                    # Only override amount for deal types — not Dividend/Buyback
                    # (Groq extracts deal_value from articles which may be profit
                    # figures, not per-share amounts. Bug #4, #26)
                    if (clf.get("deal_value_usd_m") and
                            raw[idx].get("action_type") not in ("Dividend", "Buyback")):
                        raw[idx]["amount"]   = clf["deal_value_usd_m"]
                        raw[idx]["currency"] = "USD"

    # Step 4: Apply filters
    from filters import (pre_filter, WEB_SEARCH_TRIGGER_TYPES,
                         is_non_india_geography_investment, is_ipo_mismatch)
    pre_count    = len(raw)
    filtered_raw = []
    for a in raw:
        source  = a.get("source","")
        atype   = a.get("action_type","Other")
        is_news = any(s in source for s in ["Google News","News —","Groq web",
                                             "Moneycontrol","Economic",
                                             "Business Standard","Mint"])

        if is_news:
            headline = a.get("headline","")

            # ── Deterministic override 1: non-India geography investment ──────
            # Groq-8b sometimes marks "Cargill invests in France" as INR-relevant.
            # Code always knows France ≠ India.
            if is_non_india_geography_investment(headline):
                a["_inr_involved"] = False

            # ── Deterministic override 2: IPO subject mismatch ────────────────
            # Groq-8b sees "Cerebras IPO, Apple vs Big Tech" and attributes the
            # IPO to Apple India. Check who the actual IPO entity is.
            if atype == "IPO":
                pre     = a.get("_pre_matched") or {}
                subname = (pre.get("indian_subsidiary","") or
                           pre.get("client_group","") or
                           a.get("company_name",""))
                if subname and is_ipo_mismatch(headline, subname):
                    a["_is_primary_subject"] = False
                    a["_groq_significant"]   = False

        if is_news and not a.get("_inr_involved", True):           continue
        if is_news and a.get("_skip_india_india", False):          continue
        if atype == "Dividend" and is_news and not a.get("_indian_sub_div", True): continue
        if is_news and not a.get("_is_primary_subject", True):     continue
        if is_news and not a.get("_groq_significant", True):       continue
        filtered_raw.append(a)
    print(f"  Filters removed {pre_count - len(filtered_raw)} items")
    raw = filtered_raw

    # Step 5: First-seen tracking
    try:
        fs_file  = "first_seen_cache.json"
        fs_cache = json.load(open(fs_file)) if os.path.exists(fs_file) else {}
        today    = datetime.date.today().isoformat()
        updated  = False
        for a in raw:
            key = a["headline"][:80].lower().strip()
            if key not in fs_cache:
                fs_cache[key] = today
                updated = True
            a["_first_seen"] = fs_cache[key]
        if updated:
            json.dump(fs_cache, open(fs_file,"w"))
    except Exception as ex:
        print(f"  First-seen error: {ex}")

    # Step 6: Enrich + deduplicate
    seen, enriched = set(), []
    for a in raw:
        key = a["headline"][:70].lower().strip()
        if key in seen: continue
        seen.add(key)
        if "client" not in a:
            client = a.pop("_pre_matched", None)
            if not client and a.get("ticker") and a["ticker"] in registry["by_ticker"]:
                client = registry["by_ticker"][a["ticker"]]
            if not client and a.get("company_name"):
                client = match_by_name(a["company_name"] + " " + a["headline"], registry)
            a["client"]        = client
            a["is_scb_client"] = client is not None
        elif "_pre_matched" in a:
            if not a.get("client"):
                a["client"]        = a.pop("_pre_matched")
                a["is_scb_client"] = a["client"] is not None
            else:
                a.pop("_pre_matched", None)
        enriched.append(a)

    enriched.sort(key=lambda x: x.get("date") or "", reverse=True)

    # Extract signal clients for batch priority
    # P1: any company that active_searcher found news for (including "Other" type)
    # — ensures freshly-newsworthy companies get re-Groq-searched even if RSS
    #   didn't classify them as M&A/FDI/Strategic yet
    _rss_sources = {"Google News", "Yahoo Finance", "News — India"}
    signal_clients = set()
    for a in enriched:
        c = a.get("client")
        if not c:
            continue
        key = f"{c.get('client_group','')}|{c.get('indian_subsidiary','')}"
        if a.get("action_type") in WEB_SEARCH_TRIGGER_TYPES:
            signal_clients.add(key)                        # always include trigger types
        elif a.get("source","") in _rss_sources and a.get("_pre_matched"):
            signal_clients.add(key)                        # RSS (any source) found something

    scb = sum(1 for a in enriched if a["is_scb_client"])
    print(f"After dedup: {len(enriched)} unique | {scb} SCB clients")
    print(f"Signal clients: {len(signal_clients)}")
    print(f"{'='*60}\n")
    return enriched, signal_clients, total_scanned
