"""
client_registry.py
------------------
Loads SCB_FX_Pipeline_v7.xlsx and builds two lookup structures:

1. CLIENTS_BY_TICKER  — { "MARUTI": { client record } }
2. CLIENTS_BY_NAME    — { "maruti suzuki india": { client record } }

Each client record looks like:
{
    "client_group":       "SUZUKI MOTOR CORP",
    "indian_subsidiary":  "Maruti Suzuki India Ltd",
    "cin":                "L34103DL1981PLC011375",
    "ticker":             "MARUTI",
    "exposure_usd_m":     4500,
    "priority_tier":      "TIER 1 — Pitch Immediately",
    "priority_score":     18,
    "investment_type":    "FDI Equity (Listed ~56%)",
    "event_flag":         "...",
    "notes":              "...",
    "nih_status":         "CLEAN — Full NIH Opportunity",
    "ecb_outstanding":    None,
    "net_nih_exposure":   4500,
}
"""

import re
import pandas as pd
import streamlit as st
from ticker_map import TICKER_MAP   # hand-curated NSE tickers

EXCEL_PATH = "SCB_FX_Pipeline_v7.xlsx"


def _load_sheet(sheet_name: str) -> pd.DataFrame:
    try:
        # Try header=0 first (Full Pipeline, TIER 2 sheets)
        df = pd.read_excel(EXCEL_PATH, sheet_name=sheet_name, header=0)
        # If first column looks like a header row value, use header=1
        first_val = str(df.iloc[0, 0]) if len(df) > 0 else ""
        if "SCB FX PIPELINE" in first_val or "TIER" in first_val[:20]:
            df = pd.read_excel(EXCEL_PATH, sheet_name=sheet_name, header=1)
        df = df.dropna(subset=[df.columns[0]])
        # Filter out rows that are themselves headers
        df = df[~df.iloc[:, 0].astype(str).str.contains(
            "Client Group Name|SCB FX", na=False)]
        return df
    except Exception as e:
        print(f"Could not load sheet {sheet_name}: {e}")
        return pd.DataFrame()


def _build_record(row: pd.Series) -> dict:
    """Normalise a raw Excel row into a clean client record."""
    cols = list(row.index)

    def get(keywords):
        """Find first column whose name contains any keyword."""
        for kw in keywords:
            for c in cols:
                if kw.lower() in str(c).lower():
                    val = row[c]
                    if pd.notna(val) and str(val).strip() not in ("", "None"):
                        return str(val).strip()
        return None

    client_group      = get(["Client Group Name", "Client Group"])
    indian_subsidiary = get(["Indian Subsidiary Name", "Indian Subsidiary"])
    cin               = get(["CIN Number", "CIN"])
    notes             = get(["Notes / FX"])
    event_flag        = get(["Event Flag"])
    investment_type   = get(["Investment Type"])
    priority_tier     = get(["Priority Tier", "v7 Priority Tier"])
    nih_status        = get(["NIH Status"])

    try:
        exposure = float(get(["Est. India Asset Exposure"]) or 0)
    except (ValueError, TypeError):
        exposure = 0

    try:
        priority_score = int(float(get(["v7 Priority", "Priority Score"]) or 0))
    except (ValueError, TypeError):
        priority_score = 0

    try:
        ecb = float(get(["ECB Outstanding"]) or 0) or None
    except (ValueError, TypeError):
        ecb = None

    try:
        net_nih = float(get(["Net NIH"]) or exposure) or exposure
    except (ValueError, TypeError):
        net_nih = exposure

    # Look up NSE ticker from our hand-curated map
    ticker = None
    if indian_subsidiary:
        name_key = indian_subsidiary.strip().lower()
        for map_name, map_ticker in TICKER_MAP.items():
            if map_name.lower() in name_key or name_key in map_name.lower():
                ticker = map_ticker
                break

    return {
        "client_group":      client_group,
        "indian_subsidiary": indian_subsidiary,
        "cin":               cin,
        "ticker":            ticker,
        "exposure_usd_m":    exposure,
        "priority_tier":     priority_tier or "TIER 3",
        "priority_score":    priority_score,
        "investment_type":   investment_type or "",
        "event_flag":        event_flag or "",
        "notes":             (notes or "")[:200],
        "nih_status":        nih_status or "",
        "ecb_outstanding":   ecb,
        "net_nih_exposure":  net_nih,
    }


@st.cache_data(ttl=3600)
def load_registry() -> dict:
    """
    Returns a dict with:
        "by_ticker":  { "MARUTI": record, ... }
        "by_name":    { "maruti suzuki india ltd": record, ... }
        "all":        [ record, ... ]  sorted by priority score desc
        "tier1":      [ records with TIER 1 ]
    """
    all_records = []
    seen_subs   = set()  # deduplicate by subsidiary name

    # Load Tier 1 first (highest priority), then Full Pipeline
    for sheet in ["TIER 1 — Pitch Now (v7)", "Full Pipeline",
                  "TIER 2 — Build Note", "v3 Corrections"]:
        df = _load_sheet(sheet)
        if df.empty:
            continue
        for _, row in df.iterrows():
            rec = _build_record(row)
            if not rec["client_group"] or not rec["indian_subsidiary"]:
                continue
            sub_key = rec["indian_subsidiary"].lower().strip()
            if sub_key in seen_subs:
                continue
            seen_subs.add(sub_key)
            all_records.append(rec)

    # Sort by priority score descending
    all_records.sort(key=lambda r: r["priority_score"], reverse=True)

    by_ticker = {}
    by_name   = {}

    for rec in all_records:
        if rec["ticker"]:
            by_ticker[rec["ticker"]] = rec
        if rec["indian_subsidiary"]:
            by_name[rec["indian_subsidiary"].lower().strip()] = rec

    tier1 = [r for r in all_records if "TIER 1" in r["priority_tier"]]

    print(f"Registry loaded: {len(all_records)} companies, "
          f"{len(by_ticker)} with tickers, {len(tier1)} Tier 1")

    return {
        "by_ticker": by_ticker,
        "by_name":   by_name,
        "all":       all_records,
        "tier1":     tier1,
    }


# Companies that are commonly confused — never cross-match these
NEVER_MATCH = {
    "basf":         ["bayer", "bayercrop", "bayer cropscience"],
    "bayer":        ["basf"],
    "abbvie":       ["abbott", "abbotindia"],
    "abbott":       ["abbvie"],
    "siemens energy": ["siemens"],
    "siemens":      ["siemens energy"],
    "linde":        ["lindeindia"],      # global Linde vs Linde India
    "novartis":     [],                  # fine as-is
    "pfizer":       ["piramal"],
    "glaxo":        ["glaxosmithkline consumer", "haleon"],
    "shell":        ["shell india"],
    "unilever":     [],
}

# Keywords that indicate article is market commentary, not a corporate action
MARKET_COMMENTARY_PHRASES = [
    "share price live", "stock price live", "live update", "live updates",
    "price performance", "performance snapshot", "market behavior",
    "rated buy", "rated sell", "rated hold", "rates buy", "rates sell",
    "price target", "initiates coverage", "upgrades to", "downgrades to",
    "outperform", "underperform", "neutral rating",
    "trades below issue price", "stock falls", "stock rises",
    "trades below ipo", "below ipo price", "ipo performance",
    "how india's biggest ipos", "biggest ipos performing",
]

def _is_market_commentary(headline: str) -> bool:
    h = headline.lower()
    return any(p in h for p in MARKET_COMMENTARY_PHRASES)

def _is_secondary_reference(headline: str, client_name: str) -> bool:
    """
    Returns True if the client appears as a secondary reference
    (e.g. 'Anthropic adds Novartis CEO to board' → Novartis is secondary)
    """
    h    = headline.lower()
    name = client_name.lower().replace(" group","").replace(" ltd","").strip()
    if not name or len(name) < 4:
        return False

    # Patterns where client is clearly secondary
    secondary_patterns = [
        f"adds {name}",
        f"hires {name}",
        f"appoints {name}",
        f"{name} ceo joins",
        f"{name} executive joins",
        f"{name} cfo joins",
        f"ex-{name}",
        f"former {name}",
    ]
    return any(p in h for p in secondary_patterns)


def match_by_name(text: str, registry: dict) -> dict | None:
    """
    Fuzzy-match text against known subsidiary/group names.
    Returns best matching client record or None.

    Fixes:
    - Minimum fragment length of 6 chars (was 4 — caught too many false positives)
    - Never cross-match known confused pairs (BASF/Bayer, Abbott/AbbVie etc)
    - Skip market commentary headlines entirely
    - Skip secondary-reference patterns
    """
    if _is_market_commentary(text):
        return None

    text_lower = text.lower()
    best_match = None
    best_score = 0

    STOP = {"ltd","pvt","limited","india","private","of","the","and","group",
            "corp","corporation","holdings","plc","ag","sa","bv","inc"}

    for name, rec in registry["by_name"].items():
        # Clean the name to its core searchable parts
        words     = [w for w in re.split(r'\W+', name.lower()) if w not in STOP]
        core_name = " ".join(words).strip()
        if len(core_name) < 6:
            continue

        if core_name not in text_lower:
            continue

        score = len(core_name)

        # Check NEVER_MATCH exclusions
        excluded = False
        for k, excl_list in NEVER_MATCH.items():
            if k in core_name:
                for excl in excl_list:
                    if excl in text_lower:
                        excluded = True
                        break
            if excluded:
                break
        if excluded:
            continue

        # Check if client is secondary reference
        grp = rec.get("client_group","")
        if _is_secondary_reference(text, grp):
            continue

        if score > best_score:
            best_score = score
            best_match = rec

    # Fallback: try matching on client group name
    if not best_match:
        for rec in registry["all"]:
            grp   = rec.get("client_group","") or ""
            words = [w for w in re.split(r'\W+', grp.lower()) if w not in STOP]
            core  = " ".join(words).strip()
            if len(core) < 6:
                continue
            if core not in text_lower:
                continue
            # Check secondary reference
            if _is_secondary_reference(text, grp):
                continue
            best_match = rec
            break

    return best_match
