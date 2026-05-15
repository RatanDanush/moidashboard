"""
deep_dive.py
------------
On-demand historical search for a specific client.
Searches Google News RSS + Indian news sites for past 12 months.
Groq reads results and extracts only significant corporate actions.
"""

import os, re, datetime, requests, feedparser
import streamlit as st
from dotenv import load_dotenv

load_dotenv()
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")

HDR = {"User-Agent": "Mozilla/5.0 (SCB-Dashboard)", "Accept": "application/xml, */*"}

DEEP_DIVE_PROMPT = """You are a senior financial markets analyst at Standard Chartered Bank.

You have been given news headlines about a specific company over the past 12 months.
Your job is to extract ONLY the significant corporate actions and events that have FX or 
financial markets implications.

For each significant event, return a JSON array with objects containing:
- date: best estimate of date (YYYY-MM-DD or YYYY-MM if exact date unknown)
- action_type: one of [Dividend, M&A, FDI, Buyback, Strategic, IPO, Delisting, Restructuring, Other]
- headline: clean one-line description of what happened
- fx_implication: one sentence on the FX/financial implication for Standard Chartered
- significance: High / Medium / Low
- source_hint: which headline this came from (first 5 words)

Rules:
- Only include events with genuine financial markets significance
- Ignore routine operational news, product launches, CSR activities
- Flag anything involving foreign parent, repatriation, acquisition, capital raise
- Return ONLY valid JSON array, no other text
- If no significant events found, return empty array []
- Maximum 15 events
"""


def _google_news_rss(query: str, months_back: int = 12) -> list:
    """Search Google News RSS for a query."""
    encoded = query.replace(" ", "+").replace("&", "%26")
    url = f"https://news.google.com/rss/search?q={encoded}&hl=en-IN&gl=IN&ceid=IN:en"
    items = []
    try:
        resp = requests.get(url, headers=HDR, timeout=12)
        feed = feedparser.parse(resp.content)
        cutoff = datetime.date.today() - datetime.timedelta(days=months_back * 30)

        for e in feed.entries[:30]:
            title = e.get("title", "")
            pub   = e.get("published", "")
            link  = e.get("link", "")
            date_str = datetime.date.today().isoformat()
            if pub:
                try:
                    from email.utils import parsedate_to_datetime
                    d = parsedate_to_datetime(pub).date()
                    if d < cutoff:
                        continue
                    date_str = d.isoformat()
                except Exception:
                    pass
            if title:
                items.append({
                    "title":    title,
                    "date":     date_str,
                    "url":      link,
                    "source":   "Google News",
                })
    except Exception as ex:
        print(f"Google News RSS error: {ex}")
    return items


def _fetch_all_headlines(client_group: str, indian_subsidiary: str) -> list:
    """Build multiple search queries and fetch headlines."""
    all_items = []

    # Clean names for search
    sub_clean   = re.sub(r'\b(india|limited|ltd|pvt|private|of|the)\b', '',
                         indian_subsidiary, flags=re.IGNORECASE).strip()
    group_clean = re.sub(r'\b(group|corp|corporation|holdings|plc|ag|sa)\b', '',
                         client_group, flags=re.IGNORECASE).strip()

    queries = [
        f'"{sub_clean}" acquisition OR dividend by indian subsidiary OR investment OR merger, all involving cross border INR FX flows or opportunities for the same',
        f'"{sub_clean}" India deal OR stake OR capital',
        f'"{group_clean}" India acquisition OR investment OR subsidiary',
    ]

    # Also try key financial news sites directly
    et_queries = [
        f"https://economictimes.indiatimes.com/markets/stocks/rssfeeds/2146842.cms",
        f"https://www.business-standard.com/rss/companies-101.rss",
        f"https://www.moneycontrol.com/rss/corporatenews.xml",
    ]

    seen = set()
    for q in queries:
        items = _google_news_rss(q, months_back=13)
        for item in items:
            key = item["title"][:50].lower()
            if key not in seen:
                seen.add(key)
                all_items.append(item)

    # Also scan broad feeds for name matches
    sub_words = [w for w in sub_clean.lower().split() if len(w) > 4]
    grp_words = [w for w in group_clean.lower().split() if len(w) > 4]
    search_words = sub_words + grp_words

    for url in et_queries:
        try:
            resp = requests.get(url, headers=HDR, timeout=10)
            feed = feedparser.parse(resp.content)
            cutoff = datetime.date.today() - datetime.timedelta(days=13 * 30)

            for e in feed.entries[:50]:
                title = e.get("title", "")
                summary = e.get("summary", "")
                combined = (title + " " + summary).lower()

                if not any(w in combined for w in search_words):
                    continue

                pub = e.get("published", "")
                date_str = datetime.date.today().isoformat()
                if pub:
                    try:
                        from email.utils import parsedate_to_datetime
                        d = parsedate_to_datetime(pub).date()
                        if d < cutoff:
                            continue
                        date_str = d.isoformat()
                    except Exception:
                        pass

                key = title[:50].lower()
                if key not in seen and title:
                    seen.add(key)
                    all_items.append({
                        "title":   title,
                        "date":    date_str,
                        "url":     e.get("link", ""),
                        "source":  url.split("/")[2],
                    })
        except Exception as ex:
            print(f"Deep dive feed error: {ex}")

    return all_items


def _groq_extract(headlines: list, client_group: str,
                  indian_subsidiary: str) -> list:
    """Use Groq to extract significant events from raw headlines."""
    if not GROQ_API_KEY or not headlines:
        return []

    try:
        from groq import Groq
        from token_tracker import can_afford, record_usage

        if not can_afford("deep_dive", 2500):
            print("  Deep dive: token budget exhausted, skipping Groq extraction")
            return []

        client = Groq(api_key=GROQ_API_KEY)

        # Build headline list for Groq
        hl_text = "\n".join([
            f"[{h['date']}] {h['title']}"
            for h in sorted(headlines, key=lambda x: x["date"], reverse=True)[:50]
        ])

        user_msg = (
            f"Company: {indian_subsidiary} (MNC parent: {client_group})\n\n"
            f"Headlines from the past 12 months:\n{hl_text}\n\n"
            f"Extract all significant corporate actions(FDI & Strategic Investments involving cross border INR FX flows or opportunities for future INR FX Flows, Dividends announced in last 2 months only if by Indian subsidiary, . Return JSON array only."
        )

        resp = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": DEEP_DIVE_PROMPT},
                {"role": "user",   "content": user_msg},
            ],
            temperature=0.1,
            max_tokens=2000,
        )

        # Record actual usage
        actual = getattr(resp, "usage", None)
        used   = actual.total_tokens if actual else 2500
        record_usage("deep_dive", used)

        raw = resp.choices[0].message.content.strip()
        # Strip markdown code fences if present
        raw = re.sub(r'^```(?:json)?\s*', '', raw, flags=re.MULTILINE)
        raw = re.sub(r'\s*```$', '', raw, flags=re.MULTILINE)

        import json
        events = json.loads(raw.strip())
        return events if isinstance(events, list) else []

    except Exception as ex:
        print(f"Groq deep dive error: {ex}")
        return []


@st.cache_data(ttl=3600 * 4)   # cache 4 hours
def run_deep_dive(client_group: str, indian_subsidiary: str,
                  exposure_usd_m: float) -> dict:
    """
    Main entry point for deep dive.
    Returns dict with:
      headlines: raw list
      events:    Groq-extracted significant events
      searched_at: timestamp
    """
    # Try Gemini first (grounded search reads full articles — better quality)
    # Falls back to RSS + Groq if GEMINI_API_KEY not set
    events = []
    try:
        from gemini_engine import deep_dive_search, GEMINI_API_KEY
        if GEMINI_API_KEY:
            events = deep_dive_search(client_group, indian_subsidiary)
            headlines = []   # Gemini searches internally — no separate RSS fetch
    except Exception as ex:
        print(f"  Gemini deep dive failed, falling back to Groq: {ex}")

    if not events:
        headlines = _fetch_all_headlines(client_group, indian_subsidiary)
        events    = _groq_extract(headlines, client_group, indian_subsidiary)
    else:
        headlines = []

    # Sort events by date descending, significance first
    sig_order = {"High": 0, "Medium": 1, "Low": 2}
    events.sort(key=lambda e: (
        sig_order.get(e.get("significance", "Low"), 2),
        e.get("date", "")
    ), reverse=False)
    events.sort(key=lambda e: e.get("date", ""), reverse=True)

    return {
        "headlines":    headlines,
        "events":       events,
        "searched_at":  datetime.datetime.now().strftime("%d %b %Y %H:%M"),
        "query_count":  len(headlines),
    }
