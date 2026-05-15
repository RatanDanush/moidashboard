"""
app.py — SCB Corporate Actions Intelligence  v5
------------------------------------------------
Changes:
  - Grouped by MNC parent (highest score leads, others collapse)
  - NEW flag (first_seen within 7 days AND event_date within 30 days)
  - Bigger dates, better readability
  - Google News fallback link for all items
  - Batch runs AFTER main feed loads (non-blocking)
  - INR filter applied upstream in fetcher
"""

import streamlit as st
import pandas as pd
import re
import datetime
import urllib.parse
from datetime import datetime as dt, timedelta
from streamlit_autorefresh import st_autorefresh

from client_registry import load_registry
from corporate_fetcher import fetch_all_corporate_actions
from groq_engine import fx_implication, GROQ_API_KEY
from batch_manager import run_next_batch, get_progress, load_cache
from deep_dive import run_deep_dive
from token_tracker import get_status as get_token_status

# ─── Page config ─────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="SCB — Corporate Actions Intelligence",
    page_icon="🏦",
    layout="wide",
)
st_autorefresh(interval=30 * 60 * 1000, key="corp_refresh")

# ─── Styling ─────────────────────────────────────────────────────────────────
st.markdown("""
<style>
  /* ── Global tightening ──────────────────────────────────────── */
  .block-container{padding-top:0.5rem !important;padding-bottom:0.5rem !important;}
  .stTabs [data-baseweb="tab-list"]{gap:0;border-bottom:1px solid #1a1a1a;}
  .stTabs [data-baseweb="tab"]{padding:6px 16px;font-size:11px;font-weight:600;
    letter-spacing:.06em;color:#444;}
  .stTabs [aria-selected="true"]{color:#ff6600 !important;
    border-bottom:2px solid #ff6600 !important;}

  /* ── Bloomberg header bar ───────────────────────────────────── */
  .bb-header{
    background:#000;border-bottom:2px solid #ff6600;
    padding:5px 10px 5px 10px;
    display:flex;align-items:center;justify-content:space-between;
    margin-bottom:0;
  }
  .bb-title{font-size:13px;font-weight:700;color:#e8e8e8;letter-spacing:.08em;}
  .bb-sub{font-size:9px;color:#3a3a3a;margin-left:10px;letter-spacing:.05em;}
  .bb-stats{font-size:9px;color:#3a3a3a;font-family:monospace;text-align:right;
    letter-spacing:.04em;}
  .bb-stat-hi{color:#ff6600;font-weight:700;}
  .bb-stat-ok{color:#43a047;font-weight:600;}
  .bb-stat-warn{color:#e65100;font-weight:600;}

  /* ── Section labels ─────────────────────────────────────────── */
  .bb-section{font-size:9px;font-weight:700;color:#3a3a3a;letter-spacing:.12em;
    padding-bottom:4px;border-bottom:1px solid #181818;margin-bottom:5px;}

  /* ── Event rows (inside expanders) ─────────────────────────── */
  .bb-row-crit{background:#0a0000;border-left:3px solid #ef5350;
    padding:7px 10px;border-radius:0 3px 3px 0;margin:0;}
  .bb-row-high{background:#090400;border-left:3px solid #ff8c00;
    padding:7px 10px;border-radius:0 3px 3px 0;margin:0;}
  .bb-row-med {background:#00040a;border-left:3px solid #1976d2;
    padding:7px 10px;border-radius:0 3px 3px 0;margin:0;}
  .bb-row-low {background:#050505;border-left:3px solid #222;
    padding:7px 10px;border-radius:0 3px 3px 0;margin:0;}

  /* ── FX signal bar ──────────────────────────────────────────── */
  .bb-fx{background:#00040a;border-left:2px solid #1565c0;
    padding:4px 9px;border-radius:0 2px 2px 0;
    font-size:11px;color:#5c9bd6;margin:5px 0;line-height:1.45;}
  .bb-fx-lbl{font-size:8px;font-weight:700;color:#1565c0;
    letter-spacing:.07em;margin-right:5px;}

  /* ── Sub-event rows ─────────────────────────────────────────── */
  .bb-sub{border-top:1px solid #0f0f0f;margin-top:5px;padding-top:4px;}

  /* ── Non-pipeline box ───────────────────────────────────────── */
  .nc-box{background:#050505;border:1px solid #151515;border-radius:3px;
    padding:8px 10px;}
  .nc-item{padding:4px 0;border-bottom:1px solid #0f0f0f;}
  .nc-item:last-child{border-bottom:none;}

  /* ── Deep dive cards ────────────────────────────────────────── */
  .dd-event{border-radius:3px;padding:8px 10px;margin-bottom:5px;border:1px solid #1a1a1a;}
  .dd-high{border-left:3px solid #ef5350;background:#0a0000;}
  .dd-medium{border-left:3px solid #ff8c00;background:#090400;}
  .dd-low{border-left:3px solid #222;background:#050505;}

  /* ── Pill badges ────────────────────────────────────────────── */
  .pill{display:inline-block;padding:1px 7px;border-radius:2px;
    font-size:9px;font-weight:700;margin-right:3px;letter-spacing:.04em;}
  .p-div{background:#0d200d;color:#66bb6a;}
  .p-divsp{background:#0a1e0a;color:#a5d6a7;border:1px solid #2e7d32;}
  .p-ma{background:#200d0d;color:#ef9a9a;}
  .p-fdi{background:#0d1525;color:#90caf9;}
  .p-buy{background:#201508;color:#ffcc80;}
  .p-strat{background:#1a1805;color:#fff176;}
  .p-split{background:#151515;color:#666;}
  .p-ipo{background:#0d0d20;color:#b39ddb;}

  /* Groq / source badges */
  .groq-verified{background:#0a1a0a;border:1px solid #2e7d32;color:#43a047;
    padding:1px 6px;border-radius:2px;font-size:9px;font-weight:600;}
  .groq-pending{background:#0f0f0f;border:1px solid #1a1a1a;color:#333;
    padding:1px 6px;border-radius:2px;font-size:9px;}
  .src-link{font-size:10px;color:#333;text-decoration:none;}
  .src-link:hover{color:#5c9bd6;}

  @keyframes pulse-r{
    0%,100%{border-left-color:#ef5350;}
    50%{border-left-color:#ff8a80;box-shadow:0 0 6px 1px rgba(239,83,80,.1);}
  }
  .bb-row-crit{animation:pulse-r 3s ease-in-out infinite;}
</style>
""", unsafe_allow_html=True)


# ─── Scoring ─────────────────────────────────────────────────────────────────

def is_special_div(a):
    h   = (a.get("headline","") + " " + a.get("raw_detail","")).lower()
    amt = a.get("amount") or 0
    return (any(w in h for w in ["special","extraordinary","one-time","interim",
                                  "repatriat","bumper","enhanced"])
            or amt >= 15)

ACTION_SCORES = {
    "Dividend_Special":30,"M&A":30,"FDI":28,"Dividend":15,
    "Buyback":20,"Strategic":10,"IPO":20,"Stock Split":5,"Other":3,
}

def score_action(a):
    total = 40 if a.get("is_scb_client") else 0
    atype = a.get("action_type","Other")
    akey  = "Dividend_Special" if atype=="Dividend" and is_special_div(a) else atype
    total += ACTION_SCORES.get(akey, 3)

    # Exposure
    exp = (a.get("client") or {}).get("net_nih_exposure") or 0
    if exp >= 1000:   total += 20
    elif exp >= 500:  total += 12
    elif exp >= 100:  total += 6
    elif exp > 0:     total += 2

    # Non-client significant deal boost
    if not a.get("is_scb_client") and atype in ("M&A","FDI","IPO"):
        sig = a.get("_significance","")
        if sig == "High":   total += 15
        elif sig == "Medium": total += 8

    # Boost: confirmed Indian subsidiary dividend (NSE RSS or yfinance)
    # These are always genuine repatriation events
    source = a.get("source","")
    if atype in ("Dividend","Dividend_Special") and any(
            s in source for s in ["NSE","yfinance"]):
        total += 10

    # Boost: SEBI open offer trigger
    if a.get("_sebi_open_offer"):
        total += 15

    # Penalty: M&A or FDI from Google News with no India keyword in headline
    # Catches: Whirlpool Ohio, Mercedes Alabama, etc.
    if atype in ("M&A", "FDI") and "google" in source.lower():
        headline_lower = a.get("headline","").lower()
        if not any(w in headline_lower for w in
                   ["india","indian","nse","bse","mumbai","delhi","bengaluru",
                    "hyderabad","pune","chennai","open offer","sebi","inr"]):
            total -= 30

    # Recency scoring
    try:
        delta = (datetime.date.today() -
                 datetime.date.fromisoformat(a.get("date","")[:10])).days
        if delta <= 1:    total += 10
        elif delta <= 3:  total += 7
        elif delta <= 7:  total += 4
        elif delta <= 30: total += 0
        # Score decay for old events — dividends older than 90 days
        # should not lead groups over recent events
        if atype in ("Dividend","Dividend_Special","Stock Split") and delta > 90:
            total -= 15
        elif delta > 180:
            total -= 10
        # Hard cap: events older than 30 days cannot reach critical tier
        if delta > 30:
            total = min(total, 79)
    except Exception:
        pass

    # Upcoming dividend ex-date boost (Issue 7 — Huhtamaki)
    # Ex-date within 5 days is time-sensitive — push to top
    if atype in ("Dividend","Dividend_Special"):
        try:
            ev = a.get("_event_date") or a.get("date","")
            ev_delta = (datetime.date.fromisoformat(str(ev)[:10]) -
                        datetime.date.today()).days
            if -1 <= ev_delta <= 5:   total += 20  # ex-date imminent
            elif ev_delta <= 14:      total += 10  # ex-date within 2 weeks
        except Exception:
            pass

    return max(0, total)   # floor at 0

def urgency(score):
    if score >= 80: return "critical"   # raised from 70 — reduces false critical count
    if score >= 58: return "high"       # raised from 50
    if score >= 40: return "medium"     # raised from 35
    return "low"

MINIMUM_SCORE  = 25
NON_CLIENT_MIN = 35


# ─── NEW flag ─────────────────────────────────────────────────────────────────

def is_new(action: dict) -> bool:
    """
    True if:
      - first_seen within last 7 days (entered our system recently)
      - AND event_date within last 30 days (actual event is recent, not old news)
    """
    try:
        first = action.get("_first_seen","")
        if first:
            days_since_first = (datetime.date.today() -
                                datetime.date.fromisoformat(str(first)[:10])).days
            if days_since_first > 7:
                return False

        # Use Groq-extracted event date if available, else article date
        ev_date_str = action.get("_event_date") or action.get("date","")
        if ev_date_str:
            days_since_event = (datetime.date.today() -
                                datetime.date.fromisoformat(str(ev_date_str)[:10])).days
            return days_since_event <= 30
    except Exception:
        pass
    return False


# ─── Source link helper ───────────────────────────────────────────────────────

def source_link(action: dict) -> str:
    """Return a usable URL — real article URL or Google News search fallback."""
    url = action.get("url","").strip()
    if url and url.startswith("http") and len(url) > 15:
        return url
    # Build Google News search fallback
    query = action.get("headline","")[:80]
    if not query:
        query = action.get("company_name","") + " corporate action India"
    enc = urllib.parse.quote_plus(query)
    return f"https://news.google.com/search?q={enc}&hl=en-IN&gl=IN"

def source_label(action: dict) -> str:
    s = action.get("source","").split("—")[0].strip()[:20]
    colors = {"NSE":"#1565c0","yfinance":"#2e7d32","FMP":"#6a1b9a",
              "Google":"#0277bd","Groq":"#534AB7","News":"#e65100"}
    c = next((v for k,v in colors.items() if k in s), "#444")
    return f'<span style="font-size:11px;font-weight:600;color:{c};">{s}</span>'


# ─── Date display helper ──────────────────────────────────────────────────────

def format_date(date_str: str) -> str:
    """Return a large readable date with relative label."""
    try:
        d     = datetime.date.fromisoformat(str(date_str)[:10])
        delta = (datetime.date.today() - d).days
        nice  = d.strftime("%-d %b %Y")   # e.g. "16 Apr 2026"
        if delta == 0:   rel = "today"
        elif delta == 1: rel = "yesterday"
        elif delta < 0:  rel = f"in {abs(delta)}d"
        elif delta < 7:  rel = f"{delta}d ago"
        elif delta < 30: rel = f"{delta//7}w ago"
        else:            rel = f"{delta//30}mo ago"
        return nice, rel
    except Exception:
        return str(date_str)[:10], ""


# ─── UI helpers ──────────────────────────────────────────────────────────────

def action_pill(a):
    atype = a.get("action_type","Other")
    sp    = atype=="Dividend" and is_special_div(a)
    MAP   = {
        "Dividend":   ("p-div",   "Dividend"),
        "M&A":        ("p-ma",    "M&A"),
        "FDI":        ("p-fdi",   "FDI"),
        "Buyback":    ("p-buy",   "Buyback"),
        "Strategic":  ("p-strat", "Strategic"),
        "Stock Split": ("p-split", "Split"),
        "IPO":        ("p-ipo",   "IPO"),
        "Other":      ("p-split", "News"),
    }
    cls, lbl = MAP.get(atype, ("p-split", atype))
    if sp: cls, lbl = "p-divsp", "Special dividend"
    return f'<span class="pill {cls}">{lbl}</span>'

def urgency_pill(score):
    u   = urgency(score)
    MAP = {"critical":("u-critical","CRITICAL"),
           "high":("u-high","HIGH"),
           "medium":("u-medium","MEDIUM"),
           "low":("u-low","LOW")}
    cls, lbl = MAP[u]
    return f'<span class="{cls}">{lbl}</span>'

def sc_chip(score):
    u  = urgency(score)
    c  = {"critical":"#ef5350","high":"#ff6d00","medium":"#0288d1","low":"#444"}[u]
    return (f'<span class="score-chip" '
            f'style="background:{c}18;color:{c};border:1px solid {c}44;">'
            f'{score}</span>')


# ─── Group actions by MNC parent ─────────────────────────────────────────────

def group_by_mnc(actions: list) -> list:
    """
    Returns list of groups, each group = {
      "mnc_parent": str,
      "lead":       action dict (highest score),
      "others":     [remaining action dicts sorted by score desc]
    }
    Groups sorted by lead score desc.
    Actions without a client go in their own single-item groups.
    """
    from collections import defaultdict
    buckets = defaultdict(list)

    for a in actions:
        client = a.get("client") or {}
        key    = client.get("client_group","") or a.get("company_name","Unknown")
        buckets[key].append(a)

    groups = []
    for mnc, items in buckets.items():
        items_sorted = sorted(items, key=lambda x: x["_score"], reverse=True)
        groups.append({
            "mnc_parent": mnc,
            "lead":       items_sorted[0],
            "others":     items_sorted[1:],
        })

    groups.sort(key=lambda g: g["lead"]["_score"], reverse=True)
    return groups


# ─── Bloomberg-style card renderer ───────────────────────────────────────────

def render_group(group: dict):
    lead     = group["lead"]
    others   = group["others"]
    score    = lead["_score"]
    u        = lead["_urgency"]
    client   = lead.get("client") or {}
    sp_div   = lead["action_type"] == "Dividend" and is_special_div(lead)
    new_flag = is_new(lead)

    nice_date, rel = format_date(lead.get("date", ""))

    urg_color = {"critical":"#ef5350","high":"#ff8c00",
                 "medium":"#1976d2","low":"#2a2a2a"}[u]
    row_cls   = {"critical":"bb-row-crit","high":"bb-row-high",
                 "medium":"bb-row-med","low":"bb-row-low"}[u]

    atype       = lead["action_type"]
    atype_lbl   = "SP.DIV" if sp_div else atype.upper()
    atype_color = "#a5d6a7" if sp_div else {
        "Dividend":"#66bb6a","M&A":"#ef9a9a","FDI":"#90caf9",
        "Buyback":"#ffcc80","Strategic":"#fff176",
        "Stock Split":"#888","IPO":"#b39ddb","Other":"#555"
    }.get(atype, "#888")

    amt_str = ""
    if lead.get("amount"):
        amt_str = (f"₹{lead['amount']:,.2f}/sh" if lead.get("currency")=="INR"
                   else f"${lead['amount']:,.0f}M")

    sub = client.get("indian_subsidiary", lead.get("company_name",""))[:40]
    mnc = (client.get("client_group") or group["mnc_parent"])[:32]
    nih = client.get("net_nih_exposure", 0) or 0
    inr_ok = (lead.get("_groq_confidence") in ("high","medium")
              and lead.get("_inr_involved") is True)

    # FX implication
    fx_html = ""
    if lead["is_scb_client"] and score >= 50 and GROQ_API_KEY and client:
        amt_disp = (f"₹{lead['amount']:.2f}/sh"
                    if lead.get("amount") and lead.get("currency")=="INR"
                    else f"${lead['amount']:.0f}M" if lead.get("amount") else "")
        impl = fx_implication(
            lead["headline"], lead["action_type"],
            client.get("indian_subsidiary",""),
            client.get("client_group",""),
            amt_disp,
            float(client.get("net_nih_exposure", 0) or 0),
        )
        if impl:
            fx_html = (f'<div class="bb-fx">'
                       f'<span class="bb-fx-lbl">FX ▶</span>{impl}</div>')

    # Sub-events — one per action_type
    sub_html   = ""
    seen_types = set()
    for o in others:
        ot = o.get("action_type","Other")
        if ot in seen_types: continue
        seen_types.add(ot)
        o_nice, _ = format_date(o.get("date",""))
        o_link    = source_link(o)
        sub_html += (
            f'<div style="border-top:1px solid #0f0f0f;margin-top:4px;padding-top:4px;">'
            f'<span style="font-size:9px;color:#555;font-weight:700;">{ot.upper()}</span>'
            f'&nbsp;<span style="font-size:11px;color:#555;">{o["headline"][:130]}</span>'
            f'&nbsp;<a href="{o_link}" target="_blank" class="src-link">→</a>'
            f'<span style="font-size:9px;color:#333;margin-left:5px;">{o_nice[:6]}</span>'
            f'</div>'
        )

    # Expander title — dense plain-text Bloomberg row
    star    = "★ " if u == "critical" else ""
    new_mk  = "  ◆NEW" if new_flag else ""
    more    = f"  +{len(others)}" if others else ""
    amt_mk  = f"  {amt_str}" if amt_str else ""
    d_short = nice_date[:6] if nice_date else ""
    r_short = (" (TODAY)" if rel=="today" else f" ({rel})" if rel else "")

    exp_title = (f"{star}{mnc}  ·  {atype_lbl}"
                 f"{amt_mk}  ·  {d_short}{r_short}{more}{new_mk}")

    link     = source_link(lead)
    expanded = u in ("critical","high") and lead["is_scb_client"]

    with st.expander(exp_title, expanded=expanded):
        inr_badge = (
            '<span style="font-size:9px;background:#0a1a0a;color:#43a047;'
            'padding:1px 5px;border-radius:2px;margin-left:3px;">INR✓</span>'
            if inr_ok else
            '<span style="font-size:9px;background:#0f0f0f;color:#2a2a2a;'
            'padding:1px 5px;border-radius:2px;margin-left:3px;">pend</span>'
        )
        new_badge = (
            '<span style="font-size:9px;background:#0a200a;color:#69f0ae;'
            'padding:1px 5px;border-radius:2px;margin-left:3px;font-weight:700;">NEW</span>'
            if new_flag else ""
        )
        nih_str  = (f' · <strong style="color:#66bb6a;">${nih:,.0f}M NIH</strong>'
                    if nih > 0 else "")
        amt_part = (f' · <span style="color:#a5d6a7;">{amt_str}</span>'
                    if amt_str else "")
        fe_str   = (f' · <span style="color:#ffcc80;">↔ {lead["foreign_entity"]}</span>'
                    if lead.get("foreign_entity") else "")

        st.markdown(f"""
        <div class="{row_cls}">
          <div style="display:flex;align-items:center;gap:4px;margin-bottom:4px;flex-wrap:wrap;">
            <span style="font-size:9px;font-weight:700;color:{urg_color};
                         letter-spacing:.07em;">{u.upper()}</span>
            <span style="font-size:9px;font-weight:700;color:{atype_color};
                         background:{atype_color}18;padding:1px 6px;border-radius:2px;">
              {atype_lbl}
            </span>
            <span style="font-size:9px;color:#444;background:#0d0d0d;
                         padding:1px 5px;border-radius:2px;">{score}</span>
            {new_badge}{inr_badge}
          </div>
          <div style="font-size:11px;color:#777;margin-bottom:4px;line-height:1.4;">
            <strong style="color:#ccc;">{sub}</strong>
            &nbsp;→&nbsp;<span style="color:#5c9bd6;">{mnc}</span>
            {nih_str}{amt_part}{fe_str}
            &nbsp;·&nbsp;<span style="color:#444;">{d_short}{r_short}</span>
          </div>
          <div style="font-size:12px;color:#999;line-height:1.45;margin-bottom:4px;">
            {lead["headline"][:220]}
          </div>
          {fx_html}
          <div style="margin-top:4px;">
            <a href="{link}" target="_blank" class="src-link">
              📰 {source_label(lead)} →
            </a>
          </div>
          {sub_html}
        </div>
        """, unsafe_allow_html=True)


# ─── Load registry ────────────────────────────────────────────────────────────

st.markdown("""
<div class="bb-header">
  <div>
    <span class="bb-title">🏦 SCB CORPORATE ACTIONS INTELLIGENCE</span>
    <span class="bb-sub">MNC SUBSIDIARIES · FX PIPELINE v7 · INR FLOWS ONLY</span>
  </div>
</div>
""", unsafe_allow_html=True)

with st.spinner("Loading registry..."):
    registry = load_registry()

tab_live, tab_dive = st.tabs([
        "📡  Live Monitor",
        "🔍  Client Deep Dive"
    ])


# ═══════════════════════════════════════════════════════════════════════════
# TAB 1 — LIVE MONITOR
# ═══════════════════════════════════════════════════════════════════════════
with tab_live:
  # ── Fetch main feed FIRST (user sees dashboard immediately) ───────────────
    with st.spinner("Scanning sources..."):
        raw, signal_clients, total_scanned = fetch_all_corporate_actions(registry)
    # ── Compact stats bar ─────────────────────────────────────────────────────
    try:
        from batch_manager import load_cache as _lc
        _cache_stats = _lc()
        import datetime as _dt
        _now = _dt.datetime.now()
        deep_scans_24h = sum(
            1 for k, v in _cache_stats.items()
            if k != "_meta" and v.get("last_searched") and
            (_now - _dt.datetime.fromisoformat(v["last_searched"])).total_seconds() < 86400
        )
    except Exception:
        _cache_stats  = {}
        deep_scans_24h = 0

    _prog  = get_progress(registry, _cache_stats if _cache_stats else load_cache())
    _tok   = get_token_status()
    _used  = _tok["total_used"]
    _budg  = _tok["total_budget"]
    _pct   = _tok["pct"]
    _reset = _tok["ist_reset"]
    _fresh = _prog["fresh_24h"]
    _total = _prog["total"]
    _tok_col  = "bb-stat-warn" if _pct > 80 else "bb-stat-ok"
    _cov_col  = "bb-stat-ok"   if _fresh > 0 else "bb-stat-warn"

    _sb_col, _sb_btn = st.columns([5, 1])
    with _sb_col:
        st.markdown(
            f'<div style="background:#050505;border:1px solid #151515;border-radius:2px;'
            f'padding:4px 10px;font-size:9px;font-family:monospace;color:#3a3a3a;'
            f'letter-spacing:.04em;">'
            f'ARTICLES <span class="bb-stat-hi">{total_scanned}</span>'
            f'&nbsp;&nbsp;|&nbsp;&nbsp;'
            f'GROQ COV <span class="{_cov_col}">{_fresh}/{_total}</span>'
            f'&nbsp;&nbsp;|&nbsp;&nbsp;'
            f'DEEP SCANS(24H) <span class="bb-stat-hi">{deep_scans_24h}</span>'
            f'&nbsp;&nbsp;|&nbsp;&nbsp;'
            f'TOKENS <span class="{_tok_col}">{_used:,}/{_budg:,}</span>'
            f'&nbsp;&nbsp;|&nbsp;&nbsp;'
            f'RESETS {_reset}'
            f'</div>',
            unsafe_allow_html=True)
    with _sb_btn:
        if st.button("⟳ Refresh", use_container_width=True):
            st.cache_data.clear()
            st.rerun()

    for a in raw:
        # Hard drop: Groq-marked non-significant items with no INR flow
        # Covers any action_type — catches mis-classified executive news,
        # stock commentary etc. that Groq correctly sees through
        if (a.get("_groq_significant") is False and
                a.get("_inr_involved") is False):
            a["_score"]   = 0
            a["_urgency"] = "low"
            continue
        # Also drop plain "Other" with no significance flag
        if (a.get("action_type","") == "Other" and
                a.get("_groq_significant") is False):
            a["_score"]   = 0
            a["_urgency"] = "low"
            continue
        a["_score"]   = score_action(a)
        a["_urgency"] = urgency(a["_score"])

    client_actions = sorted(
        [a for a in raw if a["is_scb_client"] and a["_score"] >= MINIMUM_SCORE],
        key=lambda x: x["_score"], reverse=True)
    non_client_actions = sorted(
        [a for a in raw if not a["is_scb_client"]
         and a["_score"] >= NON_CLIENT_MIN
         and a["action_type"] not in ("Other","Stock Split")],
        key=lambda x: x["_score"], reverse=True)

    # Filters — compact inline row
    fc1, fc2, fc3 = st.columns([2, 2, 2])
    with fc1:
        f_type = st.selectbox("Type",
            ["All"] + sorted({a["action_type"] for a in client_actions}),
            label_visibility="collapsed")
    with fc2:
        f_urg = st.selectbox("Urgency",
            ["All","Medium+","High+","Critical only"],
            label_visibility="collapsed")
    with fc3:
        new_only = st.toggle("NEW only", value=False)

    def apply_f(lst):
        if f_type != "All":  lst = [a for a in lst if a["action_type"]==f_type]
        if f_urg  == "Medium+":
            lst = [a for a in lst if a["_urgency"] in ("critical","high","medium")]
        elif f_urg == "High+":
            lst = [a for a in lst if a["_urgency"] in ("critical","high")]
        elif f_urg == "Critical only":
            lst = [a for a in lst if a["_urgency"]=="critical"]
        if new_only:
            lst = [a for a in lst if is_new(a)]
        return lst

    filtered = apply_f(client_actions)
    st.caption(
        f"**{len(filtered)}** client actions · "
        f"{len(non_client_actions)} non-pipeline · "
        f"{sum(1 for a in filtered if is_new(a))} new this week"
    )

    col_feed = st.container()
    with col_feed:
        if not filtered:
            st.info("No actions match filters. Try adjusting or refreshing.")
        else:
            groups = group_by_mnc(filtered)
            div_groups     = [g for g in groups[:60]
                              if g["lead"]["action_type"] in
                              ("Dividend","Dividend_Special","Stock Split","Buyback")]
            non_div_groups = [g for g in groups[:60]
                              if g["lead"]["action_type"] not in
                              ("Dividend","Dividend_Special","Stock Split","Buyback")]

            col_main, col_div = st.columns([3, 2], gap="small")
            with col_main:
                st.markdown(
                    '<div class="bb-section">M&A · FDI · STRATEGIC · BUYBACK · IPO</div>',
                    unsafe_allow_html=True)
                if non_div_groups:
                    for grp in non_div_groups:
                        render_group(grp)
                else:
                    st.caption("No M&A / FDI / Strategic actions.")
            with col_div:
                st.markdown(
                    '<div class="bb-section">DIVIDENDS · CORPORATE EVENTS</div>',
                    unsafe_allow_html=True)
                if div_groups:
                    for grp in div_groups:
                        render_group(grp)
                else:
                    st.caption("No dividend events.")

        # Non-client box
        if non_client_actions:
            st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)
            with st.expander(
                f"🔭  Non-pipeline companies with significant activity  "
                f"({len(non_client_actions)} found) — consider adding to Excel",
                expanded=False
            ):
                st.markdown('<div class="nc-box">', unsafe_allow_html=True)
                for a in non_client_actions[:20]:
                    nice_date, rel = format_date(a.get("date",""))
                    link = source_link(a)
                    sig_color = {"M&A":"#ef9a9a","FDI":"#90caf9","IPO":"#b39ddb",
                                 "Buyback":"#ffcc80","Strategic":"#fff176"}.get(
                                 a["action_type"],"#666")
                    amt_str = ""
                    if a.get("amount"):
                        sym = "₹" if a.get("currency")=="INR" else "$"
                        amt_str = f' · {sym}{a["amount"]:,.0f}'
                    fe_str = (f' · <span style="color:#ffcc80;">{a["foreign_entity"]}</span>'
                              if a.get("foreign_entity") else "")
                    new_html = '<span class="new-badge" style="font-size:9px;padding:1px 5px;">NEW</span> ' if is_new(a) else ""
                    st.markdown(f"""
                    <div class="nc-item">
                      <div style="display:flex;align-items:center;gap:6px;margin-bottom:3px;">
                        <span style="font-size:12px;font-weight:600;color:{sig_color};">{a['action_type']}</span>
                        <span style="font-size:13px;color:#ccc;">{a['company_name'][:50]}</span>
                        {new_html}
                        <span style="font-size:11px;color:#37474f;">{amt_str}{fe_str}</span>
                      </div>
                      <div style="font-size:12px;color:#546e7a;">
                        <strong style="color:#78909c;">{nice_date}</strong>
                        {f'<span style="color:#37474f;"> · {rel}</span>' if rel else ""}
                        &nbsp;·&nbsp; {a['headline'][:110]}
                        &nbsp;<a href="{link}" target="_blank" style="color:#37474f;font-size:11px;">→</a>
                      </div>
                    </div>
                    """, unsafe_allow_html=True)
                st.markdown('</div>', unsafe_allow_html=True)

    # Data table
    st.divider()
    with st.expander("📄 Full data table + CSV", expanded=False):
        all_rows = filtered + non_client_actions
        if all_rows:
            rows = [{
                "Score":    a["_score"],
                "Urgency":  a["_urgency"].title(),
                "New":      "✓" if is_new(a) else "",
                "Client":   "Yes" if a["is_scb_client"] else "No",
                "Date":     a.get("date",""),
                "Action":   ("Special div." if a["action_type"]=="Dividend"
                             and is_special_div(a) else a["action_type"]),
                "Company":  a.get("company_name","")[:40],
                "Headline": a.get("headline","")[:80],
                "MNC":      (a.get("client") or {}).get("client_group","—"),
                "NIH $M":   (a.get("client") or {}).get("net_nih_exposure"),
                "Source":   a.get("source","")[:25],
                "Link":     source_link(a),
            } for a in all_rows]
            df = pd.DataFrame(rows)
            st.dataframe(df, use_container_width=True, hide_index=True)
            st.download_button("⬇️ CSV",
                df.to_csv(index=False),
                f"scb_{dt.now().strftime('%Y%m%d_%H%M')}.csv","text/csv")

    # ── Run batch AFTER feed is rendered ──────────────────────────────────────
    batch_placeholder = st.empty()
    with batch_placeholder.container():
        batch_status = st.empty()
        batch_status.markdown(
            '<div style="font-size:11px;color:#263238;text-align:right;">'
            'Running web search batch...</div>',
            unsafe_allow_html=True)
    try:
        updated_cache = run_next_batch(registry, signal_clients=signal_clients)
        new_prog      = get_progress(registry, updated_cache)
        batch_status.markdown(
            f'<div style="font-size:11px;color:#1b5e20;text-align:right;">'
            f'✓ Batch complete · {new_prog["fresh_24h"]}/{new_prog["total"]} clients updated</div>',
            unsafe_allow_html=True)
    except Exception as ex:
        batch_status.markdown(
            f'<div style="font-size:11px;color:#37474f;text-align:right;">'
            f'Batch skipped: {str(ex)[:40]}</div>',
            unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════════════════════
# TAB 2 — CLIENT DEEP DIVE
# ═══════════════════════════════════════════════════════════════════════════
with tab_dive:
    st.markdown("""
    <div style="background:#0a1628;border:1px solid #1e3a5f;border-radius:10px;
                padding:14px 18px;margin-bottom:18px;">
      <strong style="color:#64b5f6;">Client Deep Dive</strong>
      <div style="font-size:12px;color:#546e7a;margin-top:4px;">
        12 months · Google News + ET + BS + Moneycontrol · Groq extracts
        INR-relevant events only
      </div>
    </div>
    """, unsafe_allow_html=True)

    options = [
        (f"{r['indian_subsidiary']}  ({r['client_group']})", r)
        for r in sorted(registry["all"],
                        key=lambda x: x.get("priority_score",0), reverse=True)
        if r.get("indian_subsidiary")
    ]

    dc1, dc2 = st.columns([3,1])
    with dc1:
        sel_label = st.selectbox("Select client", [o[0] for o in options])
    with dc2:
        run_btn = st.button("🔍 Search 12 months", use_container_width=True, type="primary")

    sel_rec = next((r for l,r in options if l==sel_label), None)

    if sel_rec:
        sub  = sel_rec["indian_subsidiary"]
        grp  = sel_rec["client_group"]
        exp  = sel_rec.get("net_nih_exposure",0) or 0
        tier = sel_rec.get("priority_tier","")
        st.markdown(f"""
        <div style="border:1px solid #1e3a5f;border-radius:8px;
                    padding:12px 16px;margin-bottom:16px;background:#06111a;">
          <div style="font-size:14px;font-weight:500;color:#eceff1;">{sub}</div>
          <div style="font-size:12px;color:#64b5f6;">{grp}</div>
          <div style="font-size:11px;color:#37474f;margin-top:4px;">
            NIH: <strong style="color:#a5d6a7;">${exp:,.0f}M</strong>
            &nbsp;·&nbsp; {tier[:25] if tier else '—'}
            &nbsp;·&nbsp; CIN: {sel_rec.get('cin','—')[:25]}
          </div>
          {"<div style='font-size:11px;color:#263238;margin-top:4px;'>Trigger: " + sel_rec.get('event_flag','')[:120] + "</div>" if sel_rec.get('event_flag') else ""}
        </div>
        """, unsafe_allow_html=True)

    if run_btn and sel_rec:
        if not GROQ_API_KEY:
            st.error("GROQ_API_KEY required for deep dive.")
        else:
            with st.spinner(f"Searching 12 months for {sub}..."):
                result = run_deep_dive(grp, sub, exp)

            st.caption(
                f"**{result['query_count']} headlines** · "
                f"**{len(result['events'])} significant events** · "
                f"{result['searched_at']}"
            )

            if not result["events"]:
                st.info("No significant INR-relevant events found.")
            else:
                m1,m2,m3 = st.columns(3)
                with m1: st.metric("Events", len(result["events"]))
                with m2: st.metric("High",
                    sum(1 for e in result["events"] if e.get("significance")=="High"))
                with m3:
                    dates = [e.get("date","") for e in result["events"] if e.get("date")]
                    if dates: st.metric("Range",
                        f"{min(dates)[:7]} → {max(dates)[:7]}")

                st.divider()
                for ev in result["events"]:
                    sig = (ev.get("significance") or "Low").strip().capitalize()
                    if sig not in ("High","Medium","Low"):
                        sig = "Low"
                    css = {"High":"dd-high","Medium":"dd-medium","Low":"dd-low"}.get(sig,"dd-low")
                    sc  = {"High":"#ef5350","Medium":"#ff6d00","Low":"#555"}.get(sig,"#555")
                    ev_link = ev.get("source_url","") or (
                        f"https://news.google.com/search?q="
                        f"{urllib.parse.quote_plus(ev.get('headline','')[:60])}"
                        f"&hl=en-IN&gl=IN")
                    nice_d, rel_d = format_date((ev.get("date") or "")[:10])
                    st.markdown(f"""
                    <div class="dd-event {css}">
                      <div style="font-size:13px;color:{sc};margin-bottom:2px;">
                        <strong>{nice_d}</strong>
                        {f'<span style="font-size:11px;color:#546e7a;"> · {rel_d}</span>' if rel_d else ""}
                        &nbsp;·&nbsp;
                        <span style="font-weight:700;">{sig.upper()}</span>
                        &nbsp;·&nbsp;
                        <span style="background:#1a2d3d;color:#90caf9;font-size:10px;
                                     font-weight:600;padding:1px 7px;border-radius:999px;">
                          {ev.get('action_type','Other')}
                        </span>
                        {"&nbsp;·&nbsp;<span style='color:#ffcc80;font-size:11px;'>" + ev['counterparty'] + "</span>" if ev.get('counterparty') else ""}
                      </div>
                      <div style="font-size:14px;font-weight:500;color:#eceff1;
                                  margin:5px 0 4px;line-height:1.45;">
                        {ev.get('headline','')}
                      </div>
                      {"<div style='font-size:12px;color:#90caf9;'>FX → " + ev['fx_implication'] + "</div>" if ev.get('fx_implication') else ""}
                      <div style="margin-top:6px;">
                        <a href="{ev_link}" target="_blank"
                           style="font-size:11px;color:#37474f;text-decoration:none;">
                           📰 Read source →
                        </a>
                      </div>
                    </div>
                    """, unsafe_allow_html=True)

            with st.expander(f"Raw headlines ({result['query_count']})", expanded=False):
                for h in sorted(result["headlines"],
                                key=lambda x: x.get("date",""), reverse=True)[:40]:
                    glink = (f"https://news.google.com/search?q="
                             f"{urllib.parse.quote_plus(h['title'][:60])}&hl=en-IN&gl=IN")
                    actual = h.get("url","")
                    link   = actual if actual.startswith("http") else glink
                    st.markdown(
                        f'<div style="font-size:12px;color:#37474f;padding:3px 0;">'
                        f'<strong style="color:#546e7a;">{h["date"]}</strong>'
                        f'&nbsp; {h["title"]}'
                        f'&nbsp;<a href="{link}" target="_blank" '
                        f'style="color:#263238;font-size:11px;">→</a>'
                        f'</div>', unsafe_allow_html=True)

