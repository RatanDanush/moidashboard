"""
gemini_engine.py
----------------
Gemini-powered intelligence engine — two-model routing strategy.

Models:
  gemini-2.5-flash-preview-05-20  →  5 RPM / 20 RPD (free tier)
    Used for: P1/P2 batch web search (signal-triggered + top Tier 1)
              Deep dive (on-demand grounded search)
              Daily briefing synthesis

  gemini-3.1-flash-lite           →  15 RPM / 500 RPD (free tier)
    Used for: P3/P4 batch web search (all remaining clients)
              Classification + noise filter (replaces Groq batch_classify)
              Dividend-specific queries

Functions:
  1. web_search_client()       — 2.5 Flash grounded search (P1/P2, top 20/day)
  2. web_search_client_lite()  — 3.1 Flash Lite grounded search (P3/P4, 500/day)
  3. gemini_classify()         — 3.1 Flash Lite batch classifier (drop-in for batch_classify)
  4. daily_briefing()          — 2.5 Flash FX desk summary
  5. deep_dive_search()        — 2.5 Flash 12-month grounded history
"""

import os, re, json, time, requests
import streamlit as st
from dotenv import load_dotenv

load_dotenv()
GEMINI_API_KEY   = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL     = "gemini-2.5-flash"                  # 5 RPM / 20 RPD  — GA model (replaces preview-05-20)
GEMINI_MODEL_LITE = "gemini-3.1-flash-lite"            # 15 RPM / 500 RPD — high throughput
_BASE_URL        = "https://generativelanguage.googleapis.com/v1beta/models"


# ─── System prompts ──────────────────────────────────────────────────────────

WEB_SEARCH_SYSTEM = """You are a corporate intelligence analyst at Standard Chartered Bank India.
Search for recent corporate actions involving the given company.

Focus ONLY on events that create cross-border INR FX flows:
- Foreign parent investing in / acquiring Indian subsidiary
- Indian subsidiary paying dividends to foreign parent (repatriation)
- M&A deals where foreign entity acquires Indian company
- Capital raises, rights issues, ECB borrowings
- Large strategic investments with foreign funding component
- Joint ventures with foreign partners involving capital flows

SKIP:
- Pure domestic Indian M&A with no foreign angle
- Operational news, product launches, hiring
- Events older than 12 months
- Analyst ratings, price targets, share price movements

Return JSON array (max 8 items):
[{
  "date": "YYYY-MM-DD or YYYY-MM",
  "event_date": "actual event date, not report date",
  "action_type": "M&A|FDI|Dividend|Strategic|IPO|Buyback|Other",
  "headline": "clean one-line description",
  "fx_implication": "one sentence — currency pair + SC product opportunity",
  "significance": "High|Medium|Low",
  "inr_involved": true|false,
  "deal_value": "e.g. $420M or Rs 3200cr or null",
  "counterparty": "name or null",
  "source_url": "article URL if found, else null"
}]

Return ONLY valid JSON array. No other text."""


BRIEFING_SYSTEM = """You are a senior financial markets analyst at Standard Chartered Bank.
Write a concise daily briefing for the FX sales desk.

Rules:
- Maximum 5 bullet points
- Format: • **Company Name** (Parent) — action — FX implication — SC product
- Infer currency pairs from company names (e.g. Siemens India = EUR/INR)
- Lead with highest FX-significance event
- Flag large or unusual events explicitly
- Mention NIH exposure where >$500M
- No fluff, no sign-off
- Only include events with clear INR cross-border angle"""


DEEP_DIVE_SYSTEM = """You are a senior financial markets analyst at Standard Chartered Bank.

Search for all corporate actions for the given company over the past 12 months.
Read the actual news articles to find deal values, counterparties, and exact dates.

Extract ONLY events with FX or financial markets implications:
- M&A where Indian entity is acquirer or target with cross-border element
- FDI: foreign parent investing fresh capital into Indian subsidiary
- Dividends declared BY the Indian subsidiary (creates INR → foreign currency flow)
- Capital raises: rights issues, QIP, preferential allotment with foreign participation
- ECB borrowings or foreign currency bonds
- Strategic JVs involving capital flows with foreign partners
- SEBI open offer triggers from upstream ownership changes

SKIP: product launches, CSR, awards, routine operational news, domestic India-India deals

Return JSON array (max 15 items):
[{
  "date": "YYYY-MM-DD or YYYY-MM",
  "action_type": "Dividend|M&A|FDI|Buyback|Strategic|IPO|Delisting|Restructuring|Other",
  "headline": "clean one-line description",
  "fx_implication": "one sentence on FX implication for Standard Chartered",
  "significance": "High|Medium|Low",
  "source_url": "article URL if found, else null"
}]

Return ONLY valid JSON array. No other text."""


# ─── Classifier prompt (for gemini_classify) ─────────────────────────────────

CLASSIFIER_SYSTEM = """You are a financial news classifier for Standard Chartered Bank India FX desk.
Classify headlines and apply strict INR relevance filters.

For each headline return a JSON object:
{
  "action_type": "M&A"|"FDI"|"Dividend"|"Buyback"|"Strategic"|"IPO"|"Delisting"|"Restructuring"|"Other",
  "confidence": "high"|"medium"|"low",
  "inr_involved": true|false,
  "skip_india_india": true|false,
  "is_indian_subsidiary_dividend": true|false,
  "is_primary_subject": true|false,
  "sebi_open_offer_trigger": true|false,
  "foreign_entity": "counterparty name or null",
  "deal_value_usd_m": number|null,
  "is_significant": true|false,
  "event_date": "YYYY-MM-DD or null"
}

inr_involved = true ONLY IF cross-border INR flow exists:
  ✅ Foreign MNC investing in Indian company
  ✅ Indian subsidiary paying dividends to foreign parent
  ✅ Foreign entity acquiring Indian company
  ✅ ECB / foreign parent capital infusion into Indian subsidiary
  ✅ Upstream foreign merger triggering SEBI open offer for listed Indian sub
  ❌ Foreign parent acquires another foreign company — even if has Indian sub
  ❌ Investment in non-India geography (Ohio, Alabama, China, EU, UAE etc)
  ❌ India-India domestic deal with no foreign funding
  ❌ Analyst ratings, price targets, share price moves
  ❌ Aggregator / list articles (Tracxn, Crunchbase, "List of N acquisitions")

is_indian_subsidiary_dividend = true ONLY if dividend declared BY Indian listed company.
skip_india_india = true if both acquirer AND target are Indian with no foreign element.
is_primary_subject = false if the matched client is only a secondary mention.
is_significant = false for: share price, analyst ratings, market commentary, stock picks.

Return ONLY a valid JSON array, one object per headline. No other text."""


# ─── Core REST API call ───────────────────────────────────────────────────────

def _call(system: str, user: str,
          use_search: bool = True,
          max_tokens: int  = 2000,
          model: str       = None) -> str:
    """
    Single Gemini API call via REST.
    model defaults to GEMINI_MODEL (2.5 Flash); pass GEMINI_MODEL_LITE for throughput.
    use_search=True enables Google Search grounding — reads full web articles.
    """
    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY not set — add to Streamlit secrets")

    _model = model or GEMINI_MODEL

    payload = {
        "system_instruction": {"parts": [{"text": system}]},
        "contents":           [{"parts": [{"text": user}], "role": "user"}],
        "generationConfig": {
            "temperature":     0.1,
            "maxOutputTokens": max_tokens,
        },
    }

    if use_search:
        payload["tools"] = [{"google_search": {}}]

    url  = f"{_BASE_URL}/{_model}:generateContent?key={GEMINI_API_KEY}"
    resp = requests.post(url, json=payload, timeout=45)

    if resp.status_code == 429:
        raise ValueError(
            f"Gemini rate limit hit ({_model}). "
            "Batch will continue on next cycle."
        )

    resp.raise_for_status()
    data  = resp.json()
    parts = (data.get("candidates", [{}])[0]
                 .get("content", {})
                 .get("parts", []))
    text  = "".join(p.get("text", "") for p in parts).strip()

    if not text:
        finish = (data.get("candidates", [{}])[0]
                      .get("finishReason", "UNKNOWN"))
        raise ValueError(f"Gemini returned empty response (finishReason: {finish})")

    return text


# ─── JSON helpers ─────────────────────────────────────────────────────────────

def _parse_json(text: str):
    """Strip markdown fences and parse JSON array from Gemini response."""
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"\s*```$",          "", text, flags=re.MULTILINE)
    # Gemini sometimes returns JSON inside prose — extract the array
    m = re.search(r"\[.*\]", text, re.DOTALL)
    if m:
        return json.loads(m.group(0))
    return json.loads(text.strip())


def _parse_amount(text: str):
    """Extract numeric deal value from strings like '$420M', 'Rs 3200cr'."""
    if not text:
        return None
    for pat, mult in [
        (r"([\d,\.]+)\s*(?:bn|billion)",      1000),
        (r"([\d,\.]+)\s*(?:mn|million|M\b)",  1),
        (r"([\d,\.]+)\s*(?:cr|crore)",        1),
    ]:
        m = re.search(pat, text, re.I)
        if m:
            try: return float(m.group(1).replace(",", "")) * mult
            except: pass
    m = re.search(r"[\$₹]\s*([\d,\.]+)", text)
    if m:
        try: return float(m.group(1).replace(",", ""))
        except: pass
    return None


# ─── 1. Web search (replaces broken Groq web_search_client) ──────────────────

def web_search_client(rec: dict) -> list:
    """
    Gemini-powered grounded web search for a specific client.

    Key improvement over Groq:
    - Google Search grounding reads full articles (not just RSS snippets)
    - Extracts actual deal values, counterparties, dates from article bodies
    - Returns search + classify + fx_implication in one call
    - Covers all 390 clients within Gemini's 1,500 RPD free limit

    Returns list of event dicts — same schema as Groq version for drop-in
    compatibility with batch_manager and get_all_cached_events.
    """
    if not GEMINI_API_KEY:
        return []

    sub    = rec.get("indian_subsidiary", "") or ""
    grp    = rec.get("client_group",       "") or ""
    exp    = rec.get("net_nih_exposure",   0)  or 0
    sector = rec.get("sector",             "") or ""

    user_msg = (
        f"Company: {sub}\n"
        f"MNC Parent: {grp}\n"
        f"Sector: {sector}\n"
        f"NIH Exposure: ${exp:,.0f}M\n\n"
        f"Search for corporate actions involving {sub} or its parent {grp} in India "
        f"in the last 12 months. Read actual articles to extract deal values, "
        f"counterparty names, and exact dates. Focus only on events that create "
        f"cross-border INR FX flows. Return as JSON array."
    )

    try:
        from token_tracker import client_already_searched, record_usage

        client_key = f"{grp}|{sub}"
        if client_already_searched(client_key):
            return []

        raw = _call(WEB_SEARCH_SYSTEM, user_msg, use_search=True)
        if not raw.strip():
            return []

        events = _parse_json(raw)
        if not isinstance(events, list):
            return []

        # Record nominal tokens to keep client deduplication working in token_tracker
        # Gemini doesn't count against Groq token budget — this is just for tracking
        record_usage("web_search", 1200, client_key)

        results = []
        for ev in events:
            if not ev.get("inr_involved", True):
                continue
            results.append({
                "company_name":   sub[:60],
                "ticker":         rec.get("ticker"),
                "action_type":    ev.get("action_type", "Other"),
                "headline":       ev.get("headline", "")[:200],
                "date":           str(ev.get("event_date") or ev.get("date", ""))[:10],
                "amount":         _parse_amount(str(ev.get("deal_value", "") or "")),
                "currency":       "USD",
                "source":         "Gemini web search",
                "raw_detail":     ev.get("fx_implication", "")[:300],
                "url":            ev.get("source_url",    "") or "",
                "foreign_entity": ev.get("counterparty"),
                "_significance":  ev.get("significance", "Medium"),
                "_inr_involved":  True,
                "_pre_matched":   rec,
            })
        return results

    except Exception as ex:
        print(f"  Gemini web search error {sub[:30]}: {ex}")
        return []


# ─── 1b. Lite web search (Gemini 3.1 Flash Lite — P3/P4 clients) ─────────────

def web_search_client_lite(rec: dict) -> list:
    """
    Gemini 3.1 Flash Lite grounded web search — P3/P4 batch queue.

    Quota: 500 RPD / 15 RPM — covers all remaining clients after the heavy pass.
    Quality is slightly lower than 2.5 Flash but sufficient for coverage search.
    Same output schema as web_search_client — drop-in for batch_manager.
    """
    if not GEMINI_API_KEY:
        return []

    sub    = rec.get("indian_subsidiary", "") or ""
    grp    = rec.get("client_group",       "") or ""
    exp    = rec.get("net_nih_exposure",   0)  or 0
    sector = rec.get("sector",             "") or ""

    user_msg = (
        f"Company: {sub}\n"
        f"MNC Parent: {grp}\n"
        f"Sector: {sector}\n"
        f"NIH Exposure: ${exp:,.0f}M\n\n"
        f"Search for corporate actions involving {sub} or {grp} in India "
        f"in the last 12 months. Focus on cross-border INR FX flows only. "
        f"Include dividends declared by the Indian subsidiary. "
        f"Return as JSON array."
    )

    try:
        from token_tracker import client_already_searched, record_usage

        client_key = f"{grp}|{sub}"
        if client_already_searched(client_key):
            return []

        raw = _call(WEB_SEARCH_SYSTEM, user_msg,
                    use_search=True,
                    max_tokens=1500,
                    model=GEMINI_MODEL_LITE)
        if not raw.strip():
            return []

        events = _parse_json(raw)
        if not isinstance(events, list):
            return []

        record_usage("web_search", 800, client_key)   # nominal — Lite uses fewer tokens

        results = []
        for ev in events:
            if not ev.get("inr_involved", True):
                continue
            results.append({
                "company_name":   sub[:60],
                "ticker":         rec.get("ticker"),
                "action_type":    ev.get("action_type", "Other"),
                "headline":       ev.get("headline", "")[:200],
                "date":           str(ev.get("event_date") or ev.get("date", ""))[:10],
                "amount":         _parse_amount(str(ev.get("deal_value", "") or "")),
                "currency":       "USD",
                "source":         "Gemini web search",
                "raw_detail":     ev.get("fx_implication", "")[:300],
                "url":            ev.get("source_url",    "") or "",
                "foreign_entity": ev.get("counterparty"),
                "_significance":  ev.get("significance", "Medium"),
                "_inr_involved":  True,
                "_pre_matched":   rec,
            })
        return results

    except Exception as ex:
        print(f"  Gemini Lite web search error {sub[:30]}: {ex}")
        return []


# ─── 1c. Batch classifier (Gemini 3.1 Flash Lite — replaces Groq batch_classify) ──

@st.cache_data(ttl=1800)   # cache 30 min — prevents 15 parallel sessions all firing 90 calls simultaneously
def gemini_classify(headlines_tuple: tuple) -> list:
    """
    Classify headlines using Gemini 3.1 Flash Lite.
    Drop-in replacement for groq_engine.batch_classify.

    Input:  tuple of (headline_str, context_str) pairs
    Output: list of classification dicts in same order

    Uses GEMINI_MODEL_LITE (500 RPD / 15 RPM) — no Groq token budget consumed.
    No Google Search grounding needed — pure classification task.

    NOTE: Called only as FALLBACK from corporate_fetcher when Groq is unavailable.
    Groq llama-3.1-8b is the primary classifier — it has no RPM limit concern and
    leaves Gemini's full 15 RPM budget for grounded web search.
    """
    if not GEMINI_API_KEY or not headlines_tuple:
        return [_classify_fallback(h) for h, _ in headlines_tuple]

    headlines = list(headlines_tuple)
    results   = []
    chunk_size = 8

    for i in range(0, len(headlines), chunk_size):
        chunk    = headlines[i:i + chunk_size]
        numbered = "\n".join(
            f"{j+1}. HEADLINE: {h}\n   CONTEXT: {s[:120]}"
            for j, (h, s) in enumerate(chunk)
        )
        user_msg = (
            f"Classify these {len(chunk)} headlines.\n"
            f"Return a JSON array of exactly {len(chunk)} objects in order:\n\n"
            f"{numbered}"
        )
        try:
            raw  = _call(CLASSIFIER_SYSTEM, user_msg,
                         use_search=False,
                         max_tokens=1000,
                         model=GEMINI_MODEL_LITE)
            data = _parse_json(raw)
            if isinstance(data, list) and len(data) == len(chunk):
                results.extend(data)
            else:
                results.extend([_classify_fallback(h) for h, _ in chunk])
        except Exception as ex:
            print(f"  Gemini classify error chunk {i}: {ex}")
            results.extend([_classify_fallback(h) for h, _ in chunk])

        if i + chunk_size < len(headlines):
            time.sleep(5)   # 15 RPM → 4s min; 5s keeps burst rate well under limit

    return results


def _classify_fallback(headline: str) -> dict:
    """Keyword fallback when Gemini unavailable."""
    h = headline.lower()
    if any(k in h for k in ["acqui","merger","takeover","stake","open offer"]):
        atype = "M&A"
    elif any(k in h for k in ["dividend","ex-date","record date","repatriat"]):
        atype = "Dividend"
    elif any(k in h for k in ["invest","fdi","capital","raise","infusion","greenfield"]):
        atype = "FDI"
    elif any(k in h for k in ["ipo","listing","drhp","delist"]):
        atype = "IPO"
    elif any(k in h for k in ["buyback","buy-back","repurchase"]):
        atype = "Buyback"
    elif any(k in h for k in ["jv","joint venture","mou","partnership"]):
        atype = "Strategic"
    else:
        atype = "Other"
    return {
        "action_type":                   atype,
        "confidence":                    "medium",
        "inr_involved":                  True,
        "skip_india_india":              False,
        "is_indian_subsidiary_dividend": atype == "Dividend",
        "is_primary_subject":            True,
        "sebi_open_offer_trigger":       False,
        "foreign_entity":                None,
        "deal_value_usd_m":              None,
        "is_significant":                atype != "Other",
        "event_date":                    None,
    }

@st.cache_data(ttl=1800)
def daily_briefing(action_snapshot: tuple) -> str:
    """
    Generate FX desk daily briefing.
    Same interface as groq_engine.daily_briefing — drop-in replacement.
    """
    if not GEMINI_API_KEY or not action_snapshot:
        return ""
    try:
        lines = []
        for s in action_snapshot:
            score, atype, hl, co, amt, curr, is_client, grp, sub, nih = s
            tag     = "SCB CLIENT" if is_client else "non-client"
            amt_str = (f"₹{amt:.2f}/sh" if amt and curr == "INR"
                       else f"${amt:.0f}M" if amt else "")
            lines.append(
                f"[Score {score}|{tag}] {atype}: {sub} (parent:{grp}) "
                f"NIH:${nih:,.0f}M {amt_str} — {hl[:100]}"
            )
        user_msg = (
            f"Today's top {len(lines)} corporate actions (INR-relevant only):\n\n"
            + "\n".join(lines)
            + "\n\nWrite the daily FX desk briefing."
        )
        return _call(BRIEFING_SYSTEM, user_msg, use_search=False, max_tokens=600)
    except Exception as ex:
        return f"_Briefing unavailable: {ex}_"


# ─── 3. Deep dive search ─────────────────────────────────────────────────────

def deep_dive_search(client_group: str, indian_subsidiary: str) -> list:
    """
    Gemini-powered deep dive: one grounded call replaces the old two-step
    (RSS fetch → Groq extraction). Reads full articles for better data quality.

    Returns list of event dicts with date, action_type, headline,
    fx_implication, significance, source_url.
    """
    if not GEMINI_API_KEY:
        return []

    user_msg = (
        f"Company: {indian_subsidiary}\n"
        f"MNC Parent: {client_group}\n\n"
        f"Search for all significant corporate actions involving {indian_subsidiary} "
        f"or its parent {client_group} in India over the last 12 months. "
        f"Read the actual news articles to find deal values, counterparties, "
        f"and exact dates. Focus on events with cross-border INR FX implications. "
        f"Return JSON array only."
    )

    try:
        raw    = _call(DEEP_DIVE_SYSTEM, user_msg, use_search=True, max_tokens=3000)
        events = _parse_json(raw)
        return events if isinstance(events, list) else []
    except Exception as ex:
        print(f"  Gemini deep dive error: {ex}")
        return []
