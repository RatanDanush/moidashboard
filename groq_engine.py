"""
groq_engine.py  v2
------------------
All Groq interactions in one place.

Functions:
  1. batch_classify()      — classify + INR filter + India-India check
  2. fx_implication()      — one-line FX implication per card
  3. is_noise()            — filter irrelevant items
  4. web_search_client()   — Groq web search for a client
  5. daily_briefing()      — FX desk summary
  6. check_inr_relevance() — standalone INR check for a single item
"""

import os, re, json, time
import streamlit as st
from dotenv import load_dotenv

load_dotenv()
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")

MODEL_FAST  = "llama-3.1-8b-instant"       # classification, noise filter
MODEL_SMART = "llama-3.3-70b-versatile"    # web search, FX, briefing, deep dive


# ─── Prompts ─────────────────────────────────────────────────────────────────

CLASSIFIER_SYSTEM = """You are a financial news classifier for Standard Chartered Bank India.
You classify headlines and apply strict relevance filters for the FX desk.

For each headline return a JSON object with:
{
  "action_type": "M&A" | "FDI" | "Dividend" | "Buyback" | "Strategic" | "IPO" | "Delisting" | "Restructuring" | "Other",
  "confidence": "high" | "medium" | "low",
  "is_india_relevant": true | false,
  "inr_involved": true | false,
  "skip_india_india": true | false,
  "is_indian_subsidiary_dividend": true | false,
  "is_primary_subject": true | false,
  "sebi_open_offer_trigger": true | false,
  "foreign_entity": "counterparty name or null",
  "deal_value_usd_m": number or null,
  "is_significant": true | false,
  "event_date": "YYYY-MM-DD or YYYY-MM or null"
}

═══ FILTER 1: INR INVOLVEMENT ═══
inr_involved = true ONLY if the transaction creates a real cross-border INR flow:
  ✅ Foreign MNC investing in / acquiring an Indian company
  ✅ Indian subsidiary paying dividends to foreign parent (repatriation)
  ✅ Foreign company acquiring Indian company (deal settled in INR)
  ✅ Indian subsidiary funded via ECB or foreign parent capital
  ✅ Indian conglomerate acquiring using foreign currency bonds
  ✅ Upstream foreign merger that triggers SEBI open offer for listed Indian subsidiary

  ❌ CRITICAL: Foreign parent acquires another foreign company → inr_involved = FALSE
     Even if the acquirer has an Indian subsidiary, that does NOT create INR flow
     Translation/accounting exposure is NOT a real FX flow
  ❌ Foreign company acquires company in a non-India country → FALSE
     (e.g. Akzo Nobel acquires Pakistan company → PKR not INR → FALSE)
  ❌ Global divestiture where Indian subsidiary is not a named party → FALSE
     (e.g. Nestlé sells global water brands → Nestlé India doesn't own water brands → FALSE)
  ❌ Any deal where both acquirer and target are outside India → FALSE
  ❌ EXPLICIT EXAMPLE: "Whirlpool Plans $60M Investment at Ohio Facility" →
     Ohio is in the USA, NOT India → inr_involved = FALSE
  ❌ EXPLICIT EXAMPLE: "Mercedes investing $4B in Alabama plant" →
     Alabama is in the USA → inr_involved = FALSE
  ❌ EXPLICIT EXAMPLE: "Company X invests in European / US / Chinese / UAE facility" →
     Any non-India geography → inr_involved = FALSE, even if Company X has an Indian arm
  ❌ GENERAL RULE: If the investment destination country is NOT India, inr_involved = FALSE.
     The existence of an Indian subsidiary of the parent does NOT change this.

═══ FILTER 2: INDIA-INDIA SKIP ═══
skip_india_india = true if:
  - Both acquirer AND target are Indian domestic entities
  - No foreign funding, no ECB, no cross-border element
  EXCEPTION — skip=false if:
  - Indian company uses ECB or foreign currency bonds for the acquisition
  - Foreign parent is funding the deal
  - Any cross-border capital movement involved

═══ FILTER 3: DIVIDEND FILTER ═══
is_indian_subsidiary_dividend = true ONLY if:
  - Dividend declared BY an Indian listed company (will flow to foreign parent)
  - Creates INR→foreign currency repatriation flow
  NOT: foreign parent global dividend announcement
  NOT: dividend mentioned in passing in a global earnings report

═══ FILTER 4: AGGREGATOR / LIST ARTICLES ═══
is_significant = false AND inr_involved = false if:
  - Article is a list of acquisitions (e.g. "List of 44 acquisitions by X")
  - Source is Tracxn, Crunchbase, CB Insights listing deals
  - Article is a database roundup, not a specific deal announcement

═══ FILTER 5: MARKET COMMENTARY / EQUITY NEWS ═══
is_significant = false AND action_type = "Other" if:
  - Share price live updates, stock performance snapshots
  - Analyst ratings: "rated buy", "rated sell", "price target", "initiates coverage"
  - Broker upgrades/downgrades, outperform/underperform calls
  - Post-IPO stock performance: "stock falls", "trades below issue price"
  - General market roundups mentioning company incidentally
  - "Share price live", "stock performance", "market cap update"
  - Stock pick / investment list articles: "Top N stocks to invest", "High-priced stocks",
    "Best stocks in India", "Stocks to watch/buy/consider" — these are retail investor
    content, NOT corporate actions. Mark inr_involved = false.
  - Telecom / utility service pricing: tariff hikes, plan revisions, price increases
    on consumer plans — these are operational revenue decisions, NOT capital flows.
    Example: "Vodafone Idea Cuts Benefits and Raises Prices on Select Plans" →
    action_type = "Other", inr_involved = false, is_significant = false

═══ FILTER 6: CLIENT AS SECONDARY REFERENCE ═══
is_primary_subject = false AND is_significant = false if:
  - The headline is primarily about a DIFFERENT company
  - The matched client appears only because an executive/person from client
    joined another company's board, or is mentioned as a secondary reference
  - Example: "Anthropic adds Novartis CEO to board" → primary subject = Anthropic,
    NOT Novartis. Mark is_primary_subject = false for Novartis match.
  - Example: "Wall Street gains; PepsiCo rises, Abbott falls 4%" → primary subject
    is market commentary, not Abbott India
  - CRITICAL EXAMPLE: "Goldman Sachs cuts Siemens Energy voting stake to below 5%"
    → primary subject = Goldman Sachs (institutional investor reducing equity position)
    → Siemens Energy / Siemens India is the OBJECT, not the actor
    → inr_involved = false (on-market equity trade, no cross-border INR flow)
    → is_primary_subject = false, is_significant = false
  - Generalised rule: if [third party] [cuts/reduces/trims/sells/raises]
    [matched company] stake/shares/holding → always is_primary_subject = false,
    inr_involved = false UNLESS the matched company is explicitly an Indian subsidiary
    selling its own shares or announcing a buyback
  - CRITICAL EXAMPLE: "Goldman Sachs cuts Siemens Energy voting stake to below 5%"
    → primary subject = Goldman Sachs (institutional investor reducing equity position)
    → Siemens Energy / Siemens India is the OBJECT, not the actor
    → inr_involved = false (on-market equity trade, no cross-border INR flow)
    → is_primary_subject = false, is_significant = false
  - Generalised rule: if [third party institution] [cuts/reduces/trims/sells/raises]
    [matched company] stake/shares/holding → always is_primary_subject = false,
    inr_involved = false UNLESS the matched company is explicitly an Indian subsidiary
    selling its own shares or announcing a buyback

═══ FILTER 7: SEBI OPEN OFFER TRIGGER ═══
sebi_open_offer_trigger = true if:
  - A foreign parent of a listed Indian company is being acquired by another entity
  - This upstream change in control triggers mandatory SEBI open offer obligations
  - Example: Akzo Nobel N.V. merging with Axalta → Akzo Nobel India faces open offer
  - This IS INR-relevant (open offer settled in INR) → inr_involved = true

═══ GENERAL RULES ═══
event_date: extract actual event date from article text (not publish date)
  Look for: "signed on", "announced on", "effective from", "declared on"
confidence = "low" for any item that is:
  - Older than 12 months
  - About a company only tangentially related to the matched client
  - Pure market commentary with no corporate action

Return ONLY a valid JSON array of objects, one per headline. No other text."""

NOISE_FILTER_SYSTEM = """You are a strict relevance filter for Standard Chartered Bank India FX desk.
Given a headline and the client it relates to, decide: is this genuinely relevant?

Return JSON: {"relevant": true|false, "reason": "brief phrase"}

ALWAYS mark as NOT relevant:
- Share price updates, live market feeds, stock performance snapshots
- Analyst ratings, broker upgrades/downgrades, price targets
- "Rated buy/sell/hold", "outperform/underperform", "initiates coverage"
- Post-IPO stock performance ("trades below issue price", "stock falls/rises X%")
- General market commentary mentioning company in passing
- The article's PRIMARY subject is a different company (client only appears as
  secondary reference — e.g. "Company X hires ex-ClientCo CEO")
- List articles / aggregator roundups (Tracxn, Crunchbase deal lists)
- Product launches, awards, CSR, marketing, operational news
- Science/R&D news with no capital markets angle
- Global deals where the Indian subsidiary is not a named party

MARK as RELEVANT:
- M&A where Indian entity is acquirer or target
- Capital raises, rights issues, FDI into Indian operations
- Dividends declared by Indian subsidiary
- SEBI open offer triggers from upstream ownership changes
- Restructuring, demerger, delisting of Indian listed entity
- Large capex with confirmed foreign funding component

Return ONLY valid JSON."""

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

Return JSON array (max 8 items):
[{
  "date": "YYYY-MM-DD or YYYY-MM",
  "event_date": "actual event date, not report date",
  "action_type": "M&A|FDI|Dividend|Strategic|IPO|Buyback|Other",
  "headline": "clean one-line description",
  "fx_implication": "one sentence — currency pair + SC product",
  "significance": "High|Medium|Low",
  "inr_involved": true|false,
  "deal_value": "e.g. $420M or ₹3200cr or null",
  "counterparty": "name or null",
  "source_url": "article URL if found, else null"
}]

Return ONLY valid JSON array. No other text."""

BRIEFING_SYSTEM = """You are a senior financial markets analyst at Standard Chartered Bank.
Write a concise daily briefing for the FX sales desk.

Rules:
- Maximum 5 bullet points
- Format: • **Company Name** (Parent) — action — FX implication — SC product
- Infer currency pairs from company names
- Lead with highest FX-significance event
- Flag large or unusual events explicitly
- Mention NIH exposure where >$500M
- No fluff, no sign-off
- Only include events with clear INR cross-border angle"""


# ─── Groq helpers ────────────────────────────────────────────────────────────

def _client():
    if not GROQ_API_KEY:
        raise ValueError("GROQ_API_KEY not set")
    from groq import Groq
    return Groq(api_key=GROQ_API_KEY)

def _call(system: str, user: str,
          model=MODEL_SMART,
          max_tokens=1200,
          temperature=0.1,
          budget_function: str = "buffer") -> str:
    """Single Groq call — tracks token usage, respects budget."""
    from token_tracker import can_afford, record_usage, remaining

    est_tokens = max_tokens + len(system)//4 + len(user)//4
    if not can_afford(budget_function, est_tokens):
        rem = remaining(budget_function)
        raise ValueError(
            f"Token budget exhausted for '{budget_function}' "
            f"(remaining: {rem} tokens). Resets at 5:30am IST."
        )

    c    = _client()
    resp = c.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user",   "content": user},
        ],
        temperature=temperature,
        max_tokens=max_tokens,
    )
    # Record actual usage
    actual = getattr(resp, "usage", None)
    used   = actual.total_tokens if actual else est_tokens
    record_usage(budget_function, used)

    return resp.choices[0].message.content.strip()

def _parse_json(text: str):
    text = re.sub(r'^```(?:json)?\s*', '', text, flags=re.MULTILINE)
    text = re.sub(r'\s*```$',          '', text, flags=re.MULTILINE)
    return json.loads(text.strip())


# ─── 1. Batch classifier (with all filters) ───────────────────────────────────

@st.cache_data(ttl=3600)
def batch_classify(headlines_tuple: tuple) -> list:
    """
    Classify headlines AND apply INR filter, India-India skip, dividend filter.
    Returns list of classification dicts in same order as input.
    """
    if not GROQ_API_KEY or not headlines_tuple:
        return [_fallback_classify(h) for h, _ in headlines_tuple]

    headlines = list(headlines_tuple)
    results   = []
    chunk_size = 5   # smaller batches → more accurate INR reasoning per headline

    for i in range(0, len(headlines), chunk_size):
        chunk    = headlines[i:i + chunk_size]
        numbered = "\n".join(
            f"{j+1}. HEADLINE: {h}\n   CONTEXT: {s[:120]}"
            for j, (h, s) in enumerate(chunk)
        )
        user_msg = (
            f"Classify these {len(chunk)} headlines.\n"
            f"Apply ALL filters (inr_involved, skip_india_india, is_indian_subsidiary_dividend).\n"
            f"Return a JSON array of exactly {len(chunk)} objects in order:\n\n"
            f"{numbered}"
        )
        try:
            raw  = _call(CLASSIFIER_SYSTEM, user_msg,
                         model=MODEL_FAST,
                         max_tokens=800,
                         budget_function="classify")
            data = _parse_json(raw)
            if isinstance(data, list) and len(data) == len(chunk):
                results.extend(data)
            else:
                results.extend([_fallback_classify(h) for h, _ in chunk])
        except Exception as ex:
            print(f"  Classifier error chunk {i}: {ex}")
            results.extend([_fallback_classify(h) for h, _ in chunk])
        if i + chunk_size < len(headlines):
            time.sleep(0.5)

    return results

def _fallback_classify(headline: str) -> dict:
    """Keyword-based fallback when Groq unavailable."""
    h = headline.lower()
    if any(k in h for k in ["acqui","merger","takeover","stake","divest","open offer"]):
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
        "action_type":               atype,
        "confidence":                "medium",
        "is_india_relevant":         True,
        "inr_involved":              True,
        "skip_india_india":          False,
        "is_indian_subsidiary_dividend": atype == "Dividend",
        "foreign_entity":            None,
        "deal_value_usd_m":          None,
        "is_significant":            atype != "Other",
        "event_date":                None,
    }


# ─── 2. FX implication ───────────────────────────────────────────────────────

@st.cache_data(ttl=3600)
def fx_implication(headline: str, action_type: str,
                   company: str, mnc_parent: str,
                   amount_str: str, nih_usd_m: float) -> str:
    if not GROQ_API_KEY:
        return ""
    try:
        user_msg = (
            f"Action: {action_type}\n"
            f"Company: {company} (subsidiary of {mnc_parent})\n"
            f"Amount: {amount_str or 'not specified'}\n"
            f"NIH exposure: ${nih_usd_m:,.0f}M\n"
            f"Headline: {headline}\n\n"
            f"Write the FX implication sentence."
        )
        result = _call(FX_IMPLICATION_SYSTEM, user_msg,
                       max_tokens=80, temperature=0.2,
                       budget_function="fx_implication")
        return result.strip('"').strip("'").strip()
    except Exception as ex:
        print(f"  FX implication error: {ex}")
        return ""


# ─── 3. Noise filter ─────────────────────────────────────────────────────────

@st.cache_data(ttl=1800)
def is_noise(headline: str, client_group: str, subsidiary: str) -> bool:
    if not GROQ_API_KEY:
        return False
    try:
        user_msg = (
            f"Client: {subsidiary} (parent: {client_group})\n"
            f"Headline: {headline}\n\nIs this relevant?"
        )
        raw  = _call(NOISE_FILTER_SYSTEM, user_msg,
                     model=MODEL_FAST, max_tokens=60,
                     budget_function="classify")
        data = _parse_json(raw)
        return not data.get("relevant", True)
    except Exception:
        return False


# ─── 4. Web search (DEPRECATED — moved to gemini_engine.py) ─────────────────

def web_search_client(rec: dict) -> list:
    """
    DEPRECATED: Groq tools[{"type": "web_search"}] returns HTTP 400.
    Groq removed the web_search tool type — only 'function' and 'mcp' are valid.
    Web search is now handled by gemini_engine.web_search_client().
    """
    sub = rec.get("indian_subsidiary", "unknown")
    print(f"  WARNING: groq_engine.web_search_client is deprecated — "
          f"use gemini_engine.web_search_client for {sub}")
    return []

def _web_search_client_original_broken(rec: dict) -> list:
    """Original Groq web search — kept for reference only. DO NOT CALL."""
    if not GROQ_API_KEY:
        return []

    sub = rec.get("indian_subsidiary", "") or ""
    grp = rec.get("client_group", "") or ""
    exp = rec.get("net_nih_exposure", 0) or 0

    user_msg = (
        f"Company: {sub}\nMNC Parent: {grp}\nNIH Exposure: ${exp:,.0f}M\n\n"
        f"Search for corporate actions involving {sub} or its parent {grp} in India, "
        f"last 12 months. Focus on cross-border INR flows only. "
        f"Return as JSON array."
    )

    try:
        from token_tracker import can_afford, record_usage, client_already_searched
        client_key = f"{grp}|{sub}"

        # Skip if already searched today
        if client_already_searched(client_key):
            return []

        # Check web search budget
        if not can_afford("web_search", 1200):
            print(f"  Web search budget exhausted — skipping {sub[:30]}")
            return []

        c = _client()
        resp = c.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": WEB_SEARCH_SYSTEM},
                {"role": "user",   "content": user_msg},
            ],
            tools=[{"type": "web_search"}],
            temperature=0.1,
            max_tokens=1500,
        )

        # Extract text content
        content = ""
        msg = resp.choices[0].message
        if isinstance(getattr(msg, 'content', None), list):
            for block in msg.content:
                if hasattr(block, 'text'):
                    content += block.text
        elif isinstance(getattr(msg, 'content', None), str):
            content = msg.content or ""

        if not content.strip():
            return []

        events = _parse_json(content)
        if not isinstance(events, list):
            return []

        # Record actual usage
        actual = getattr(resp, "usage", None)
        used   = actual.total_tokens if actual else 1200
        from token_tracker import record_usage
        record_usage("web_search", used, client_key)

        results = []
        for ev in events:
            if not ev.get("inr_involved", True):
                continue
            results.append({
                "company_name":   sub[:60],
                "ticker":         rec.get("ticker"),
                "action_type":    ev.get("action_type", "Other"),
                "headline":       ev.get("headline", "")[:200],
                "date":           str(ev.get("event_date") or ev.get("date",""))[:10],
                "amount":         _parse_amount(str(ev.get("deal_value","") or "")),
                "currency":       "USD",
                "source":         "Groq web search",
                "raw_detail":     ev.get("fx_implication","")[:300],
                "url":            ev.get("source_url","") or "",
                "foreign_entity": ev.get("counterparty"),
                "_significance":  ev.get("significance","Medium"),
                "_inr_involved":  True,
                "_pre_matched":   rec,
            })
        return results

    except Exception as ex:
        print(f"  Groq web search error {sub[:30]}: {ex}")
        return []


# ─── 5. Daily briefing ───────────────────────────────────────────────────────

@st.cache_data(ttl=1800)
def daily_briefing(action_snapshot: tuple) -> str:
    if not GROQ_API_KEY or not action_snapshot:
        return ""
    try:
        lines = []
        for s in action_snapshot:
            score, atype, hl, co, amt, curr, is_client, grp, sub, nih = s
            tag    = "SCB CLIENT" if is_client else "non-client"
            amt_str = (f"₹{amt:.2f}/sh" if amt and curr=="INR"
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
        return _call(BRIEFING_SYSTEM, user_msg,
                     max_tokens=500, temperature=0.2,
                     budget_function="briefing")
    except Exception as ex:
        return f"_Briefing unavailable: {ex}_"


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _parse_amount(text: str):
    if not text: return None
    m = re.search(r'([\d,\.]+)\s*(?:bn|billion)', text, re.I)
    if m:
        try: return float(m.group(1).replace(",","")) * 1000
        except: pass
    m = re.search(r'([\d,\.]+)\s*(?:mn|million|M\b|cr)', text, re.I)
    if m:
        try: return float(m.group(1).replace(",",""))
        except: pass
    m = re.search(r'[\$₹]\s*([\d,\.]+)', text)
    if m:
        try: return float(m.group(1).replace(",",""))
        except: pass
    return None

def make_snapshot(actions: list) -> tuple:
    top = sorted(
        [a for a in actions if a.get("_score",0) >= 50],
        key=lambda x: x["_score"], reverse=True
    )[:12]
    return tuple(
        (
            a.get("_score",0), a.get("action_type",""),
            a.get("headline","")[:100], a.get("company_name",""),
            a.get("amount"), a.get("currency"),
            a.get("is_scb_client",False),
            (a.get("client") or {}).get("client_group",""),
            (a.get("client") or {}).get("indian_subsidiary",""),
            (a.get("client") or {}).get("net_nih_exposure",0) or 0,
        )
        for a in top
    )
