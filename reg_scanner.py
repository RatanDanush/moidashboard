"""
reg_scanner.py  —  SCB Regulatory Intelligence
------------------------------------------------
Three independent scanners, each returning a uniform list of dicts.

Scanners:
  1. scan_ecb()          — RBI monthly ECB XLSX, cross-referenced vs client registry
  2. scan_cci()          — CCI combination (M&A) orders, public HTML
  3. scan_sebi_offers()  — SEBI open offer / takeover announcements, public HTML

Each result dict has keys:
  source         str   "RBI-ECB" | "CCI" | "SEBI-OO"
  date           str   YYYY-MM-DD (best available)
  company        str   borrower / target company name
  counterparty   str   acquirer / lender (if available)
  amount_usd_m   float | None
  detail         str   one-line summary
  matched_client dict | None   — registry record if fuzzy-matched
  match_score    int   0-100 (0 = unmatched)
  url            str   source URL
  fx_angle       str   one-line FX pitch hook
"""

import re
import datetime
import requests
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
import io
import difflib

import pandas as pd
import streamlit as st

HDR = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-IN,en;q=0.9",
}

# ─── Fuzzy matching ────────────────────────────────────────────────────────────

def _normalize(name: str) -> str:
    """Strip legal suffixes and common noise for cleaner fuzzy match."""
    noise = [
        r"\b(private|pvt|limited|ltd|llp|inc|corp|corporation|"
        r"india|indian|holdings|group|plc|ag|sa|gmbh|bv|nv|oy|as)\b",
        r"[^a-z0-9\s]",
    ]
    s = name.lower()
    for pat in noise:
        s = re.sub(pat, " ", s, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", s).strip()


def _fuzzy_match(name: str, registry: dict, threshold: float = 0.55):
    """
    Company-name-to-company-name matching. Does NOT use match_by_name() because
    that function has a len<6 guard designed for headlines — it silently drops
    ABB (3), 3M (2), SKF (3), BASF (4), Bosch (5) etc.

    Strategy:
    1. Token containment: find the longest token from ECB name (≥2 chars) that
       appears as a whole word in the registry candidate name.
    2. SequenceMatcher for final score; boosted based on token length
       (longer distinctive token = higher confidence floor).
    """
    norm_name = _normalize(name)
    # Tokens sorted longest-first (most distinctive first)
    tokens = sorted(
        [t for t in norm_name.split() if len(t) >= 2],
        key=len, reverse=True,
    )
    if not tokens:
        return None, 0

    best_record = None
    best_score  = 0.0

    for rec in registry.get("all", []):
        for field in ("indian_subsidiary", "client_group"):
            candidate = rec.get(field, "") or ""
            norm_cand = _normalize(candidate)
            if not norm_cand:
                continue

            cand_tokens = set(norm_cand.split())

            # Find best matching token (word-boundary match only)
            best_tok_len = 0
            for tok in tokens:
                if tok in cand_tokens:
                    best_tok_len = len(tok)
                    break

            if best_tok_len == 0:
                continue

            seq = difflib.SequenceMatcher(None, norm_name, norm_cand).ratio()

            # Confidence floor by token length:
            # ≥7 chars → "siemens","novartis","honeywell" → very distinctive → 0.80
            # ≥5 chars → "bosch","sanofi","merck"          → distinctive      → 0.73
            # ≥4 chars → "basf","bayer","3com"             → moderate         → 0.68
            # 2-3 chars → "abb","skf","3m"                 → short but exact  → 0.65
            if best_tok_len >= 7:
                score = max(seq, 0.80)
            elif best_tok_len >= 5:
                score = max(seq, 0.73)
            elif best_tok_len >= 4:
                score = max(seq, 0.68)
            else:
                score = max(seq, 0.65)

            if score > best_score:
                best_score  = score
                best_record = rec

    if best_score >= threshold:
        return best_record, int(best_score * 100)
    return None, 0


# ══════════════════════════════════════════════════════════════════════════════
# SCANNER 1 — RBI ECB
# ══════════════════════════════════════════════════════════════════════════════

ECB_INDEX_URL = "https://rbi.org.in/Scripts/ECBView.aspx"

def _get_ecb_xlsx_urls(n_months: int = 3) -> list[str]:
    """
    Scrape the RBI ECB index page to get the latest N download URLs.
    Falls back to a known recent URL pattern if the page is unreachable.
    """
    urls = []
    try:
        resp = requests.get(ECB_INDEX_URL, headers=HDR, timeout=15)
        # All XLSX download links contain /rdocs/ECB/DOCs/
        found = re.findall(
            r'https://rbidocs\.rbi\.org\.in/rdocs/ECB/DOCs/[^\s"\']+\.XLSX',
            resp.text,
            re.IGNORECASE,
        )
        # Deduplicate while preserving order (latest first on the page)
        seen = set()
        for u in found:
            if u not in seen:
                seen.add(u)
                urls.append(u)
        urls = urls[:n_months]
    except Exception as ex:
        print(f"  [ECB] Index scrape failed: {ex}")
    return urls


def _parse_ecb_xlsx(xlsx_bytes: bytes) -> list[dict]:
    """
    Parse one RBI ECB XLSX file.
    Returns list of dicts with keys: borrower, sector, amount_usd, purpose,
    maturity_years, lender_category, route.
    """
    rows = []
    try:
        xl = pd.ExcelFile(io.BytesIO(xlsx_bytes), engine="openpyxl")
        for sheet_name in xl.sheet_names:
            df = xl.parse(sheet_name, header=None)

            # Find header row — look for "Borrower" column
            header_row = None
            for i, row in df.iterrows():
                row_str = " ".join(str(v).lower() for v in row.values)
                if "borrower" in row_str and "amount" in row_str:
                    header_row = i
                    break

            if header_row is None:
                continue

            df.columns = [str(c).strip().lower() for c in df.iloc[header_row]]
            df = df.iloc[header_row + 1:].reset_index(drop=True)
            df = df.dropna(how="all")

            # Normalise column names
            col_map = {}
            for col in df.columns:
                c = str(col).lower()
                if "borrower" in c:             col_map[col] = "borrower"
                elif "sector" in c:             col_map[col] = "sector"
                elif "amount" in c or "usd" in c: col_map[col] = "amount_usd"
                elif "purpose" in c:            col_map[col] = "purpose"
                elif "maturity" in c:           col_map[col] = "maturity"
                elif "lender" in c:             col_map[col] = "lender_category"
                elif "route" in c:              col_map[col] = "route"
            df = df.rename(columns=col_map)

            required = {"borrower", "amount_usd"}
            if not required.issubset(set(df.columns)):
                continue

            for _, r in df.iterrows():
                borrower = str(r.get("borrower", "")).strip()
                if not borrower or borrower.lower() in ("nan", "borrower", ""):
                    continue
                try:
                    amt = float(str(r.get("amount_usd", 0)).replace(",", ""))
                except Exception:
                    amt = 0.0

                rows.append({
                    "borrower":       borrower,
                    "sector":         str(r.get("sector", "")).strip(),
                    "amount_usd":     amt,
                    "purpose":        str(r.get("purpose", "")).strip(),
                    "maturity":       str(r.get("maturity", "")).strip(),
                    "lender_category": str(r.get("lender_category", "")).strip(),
                    "route":          str(r.get("route", "Automatic")).strip(),
                })
    except Exception as ex:
        print(f"  [ECB] XLSX parse error: {ex}")
    return rows


def _ecb_fx_angle(row: dict, matched: bool) -> str:
    purpose    = row.get("purpose", "").lower()
    lender     = row.get("lender_category", "")
    amt        = row.get("amount_usd", 0) or 0
    amt_str    = f"${amt:,.0f}M" if amt >= 1 else ""

    if "foreign collaborator" in lender.lower() or "equity holder" in lender.lower():
        hook = "parent-company loan → drawdown likely converted to INR"
    elif "multilateral" in lender.lower():
        hook = "multilateral borrowing → likely USD→INR conversion on drawdown"
    elif "refinanc" in purpose:
        hook = "ECB refinancing → hedging of existing USD liability"
    elif "capital goods" in purpose or "import" in purpose:
        hook = "capex-linked ECB → FX payments on equipment imports"
    else:
        hook = "cross-border borrowing → drawdown/repayment FX flow"

    client_tag = "SCB client — " if matched else "Prospect — "
    return f"{client_tag}{amt_str} {hook}".strip()


@st.cache_data(ttl=3600 * 24, show_spinner=False)
def scan_ecb(registry: dict, n_months: int = 3) -> dict:
    """
    Download last N months of RBI ECB XLSXs, fuzzy-match vs registry.
    Returns {
        results: list[dict],   # all rows
        matched: list[dict],   # only SCB client matches
        prospects: list[dict], # unmatched, ≥ $5M, known MNC sectors
        fetch_date: str,
        months_covered: int,
        error: str | None
    }
    """
    results    = []
    error_msg  = None

    xlsx_urls  = _get_ecb_xlsx_urls(n_months)
    if not xlsx_urls:
        return {
            "results": [], "matched": [], "prospects": [],
            "fetch_date": datetime.date.today().isoformat(),
            "months_covered": 0,
            "error": "Could not retrieve ECB file list from RBI website.",
        }

    months_ok = 0
    for url in xlsx_urls:
        try:
            r = requests.get(url, headers=HDR, timeout=30)
            r.raise_for_status()
            rows = _parse_ecb_xlsx(r.content)
            months_ok += 1

            # Approximate month from URL filename
            month_tag = re.search(r'PR\d+ECB', url)
            month_str = month_tag.group(0) if month_tag else "Recent"

            for row in rows:
                matched_rec, score = _fuzzy_match(row["borrower"], registry)
                amt_m = row["amount_usd"] / 1_000_000 if row["amount_usd"] > 10_000 else row["amount_usd"]
                results.append({
                    "source":        "RBI-ECB",
                    "date":          datetime.date.today().isoformat(),  # month-level only
                    "company":       row["borrower"],
                    "counterparty":  row.get("lender_category", ""),
                    "amount_usd_m":  round(amt_m, 2) if amt_m else None,
                    "detail": (
                        f"{row.get('purpose','—')} · "
                        f"Maturity: {row.get('maturity','—')} · "
                        f"Route: {row.get('route','—')}"
                    ),
                    "sector":        row.get("sector", ""),
                    "matched_client": matched_rec,
                    "match_score":   score,
                    "url":           url,
                    "fx_angle":      _ecb_fx_angle(row, matched_rec is not None),
                    "_month_tag":    month_str,
                })
        except Exception as ex:
            print(f"  [ECB] Download failed {url}: {ex}")
            error_msg = str(ex)

    matched    = [r for r in results if r["matched_client"] is not None]
    # Prospects: unmatched, ≥$5M, plausible MNC sectors
    MNC_SECTORS = {
        "auto", "pharma", "chemical", "electrical", "machine",
        "power", "software", "it ", "metal", "food", "textile",
        "infra", "telecom", "consumer", "industrial",
    }
    prospects  = [
        r for r in results
        if r["matched_client"] is None
        and (r["amount_usd_m"] or 0) >= 5
        and any(s in (r.get("sector","")).lower() for s in MNC_SECTORS)
    ]

    return {
        "results":        results,
        "matched":        matched,
        "prospects":      sorted(prospects, key=lambda x: x["amount_usd_m"] or 0, reverse=True)[:30],
        "fetch_date":     datetime.date.today().isoformat(),
        "months_covered": months_ok,
        "error":          error_msg if months_ok == 0 else None,
    }


# ══════════════════════════════════════════════════════════════════════════════
# SCANNER 2 — CCI COMBINATION ORDERS
# ══════════════════════════════════════════════════════════════════════════════

CCI_ORDERS_URL = "https://www.cci.gov.in/combination/orders-section31"
CCI_GREEN_URL  = "https://www.cci.gov.in/combination/green-channel-notices"

def _parse_cci_html(html: str, source_url: str) -> list[dict]:
    """
    Extract combination order rows from CCI HTML.
    CCI uses a standard table layout — rows contain: case no, parties, date, status.
    """
    rows = []
    try:
        # Find all table rows with case data
        # CCI HTML: <td> blocks with case numbers like "C-YYYY/NNNN"
        case_blocks = re.findall(
            r'C-\d{4}/\d+.*?(?=C-\d{4}/\d+|$)',
            html.replace('\n', ' '),
            re.DOTALL,
        )

        for block in case_blocks[:50]:
            # Case number
            case_m = re.search(r'(C-\d{4}/\d+)', block)
            case_no = case_m.group(1) if case_m else ""

            # Date — looks for DD/MM/YYYY or YYYY-MM-DD patterns
            date_m = re.search(
                r'(\d{2}[/-]\d{2}[/-]\d{4}|\d{4}-\d{2}-\d{2})', block
            )
            date_str = ""
            if date_m:
                raw_d = date_m.group(1)
                try:
                    if "/" in raw_d or (raw_d[2] == "-" and raw_d[5] == "-"):
                        parts = re.split(r'[/\-]', raw_d)
                        # DD-MM-YYYY → YYYY-MM-DD
                        date_str = f"{parts[2]}-{parts[1]:>02}-{parts[0]:>02}"
                    else:
                        date_str = raw_d
                except Exception:
                    date_str = raw_d

            # Strip HTML tags to get text
            text = re.sub(r'<[^>]+>', ' ', block)
            text = re.sub(r'\s+', ' ', text).strip()

            # Try to extract party names from the cleaned text
            # CCI entries typically list "Acquirer / Target" or "Party A and Party B"
            parties = text[:300].strip()

            # Status
            status_m = re.search(
                r'(Approved|Deemed Approved|Notice Not Valid|'
                r'Approved with Modification|Transaction called off|'
                r'Notice withdrawn|Exempt)',
                text, re.IGNORECASE,
            )
            status = status_m.group(1) if status_m else "Approved"

            if not case_no:
                continue

            rows.append({
                "case_no": case_no,
                "date":    date_str or datetime.date.today().isoformat(),
                "parties": parties[:200],
                "status":  status,
                "url":     source_url,
            })
    except Exception as ex:
        print(f"  [CCI] Parse error: {ex}")
    return rows


def _cci_to_result(row: dict, registry: dict) -> dict:
    parties = row.get("parties", "")
    # Try matching both halves of "X acquires Y" pattern
    matched_rec, score = _fuzzy_match(parties, registry, threshold=0.55)

    # Extract amount if mentioned
    amt_m = None
    amt_match = re.search(
        r'\$\s*([\d,]+(?:\.\d+)?)\s*(billion|bn|million|mn|m\b)',
        parties, re.IGNORECASE
    )
    if amt_match:
        val = float(amt_match.group(1).replace(",", ""))
        unit = amt_match.group(2).lower()
        amt_m = val * 1000 if unit in ("billion", "bn") else val

    return {
        "source":         "CCI",
        "date":           row["date"],
        "company":        parties[:120],
        "counterparty":   row["case_no"],
        "amount_usd_m":   amt_m,
        "detail":         f"CCI {row['status']} · Case {row['case_no']}",
        "sector":         "",
        "matched_client": matched_rec,
        "match_score":    score,
        "url":            row["url"],
        "fx_angle": (
            "SCB client M&A — CCI clearance signals imminent deal close → "
            "capital repatriation / remittance FX flow"
            if matched_rec else
            "Pre-news M&A signal — cross-border deal requires INR conversion on close"
        ),
    }


@st.cache_data(ttl=3600 * 6, show_spinner=False)
def scan_cci(registry: dict) -> dict:
    """
    Scrape CCI combination orders page and match against registry.
    """
    all_rows  = []
    error_msg = None

    for url in [CCI_ORDERS_URL, CCI_GREEN_URL]:
        try:
            resp = requests.get(url, headers=HDR, timeout=15, verify=False)
            resp.raise_for_status()
            parsed = _parse_cci_html(resp.text, url)
            all_rows.extend(parsed)
        except Exception as ex:
            print(f"  [CCI] Fetch failed {url}: {ex}")
            error_msg = str(ex)

    # Convert and sort — most recent first
    results = [_cci_to_result(r, registry) for r in all_rows]
    results.sort(key=lambda x: x["date"], reverse=True)

    # Deduplicate by case number
    seen_cases, deduped = set(), []
    for r in results:
        key = r["counterparty"]  # case_no stored here
        if key not in seen_cases:
            seen_cases.add(key)
            deduped.append(r)

    matched   = [r for r in deduped if r["matched_client"] is not None]
    all_deals = deduped[:60]  # cap display

    return {
        "results":    all_deals,
        "matched":    matched,
        "fetch_date": datetime.date.today().isoformat(),
        "total":      len(deduped),
        "error":      error_msg if not deduped else None,
    }


# ══════════════════════════════════════════════════════════════════════════════
# SCANNER 3 — SEBI OPEN OFFERS
# ══════════════════════════════════════════════════════════════════════════════

# SEBI SAST (Takeover) public announcement listing — structured HTML table
SEBI_OO_URL = (
    "https://www.sebi.gov.in/sebiweb/other/OtherAction.do"
    "?doRecognisedFpi=yes&intmId=13"
)
# Fallback: BSE corporate announcements filtered for open offer category
BSE_OO_URL  = (
    "https://api.bseindia.com/BseIndiaAPI/api/AnnGetAnnouncementDetail/w"
    "?strCat=Takeover%20or%20Open%20Offer&strPrevDate=&strScrip=&strSearch=P"
    "&strToDate=&strFromDate=&strPer=0"
)


def _parse_sebi_oo_html(html: str) -> list[dict]:
    """
    Parse SEBI open offer table.
    Columns typically: Sr, Target Company, Acquirer, Offer Price, % Shares,
    PA Date, Open Date, Close Date
    """
    rows = []
    try:
        # Find <tr> rows within the main table
        tr_blocks = re.findall(r'<tr[^>]*>(.*?)</tr>', html, re.DOTALL | re.IGNORECASE)
        for tr in tr_blocks:
            tds = re.findall(r'<td[^>]*>(.*?)</td>', tr, re.DOTALL | re.IGNORECASE)
            if len(tds) < 4:
                continue

            # Strip HTML from each cell
            cells = [re.sub(r'<[^>]+>', '', td).strip() for td in tds]
            cells = [re.sub(r'\s+', ' ', c).strip() for c in cells]

            # Skip header rows
            if not cells[0].isdigit():
                continue

            # Flexible column assignment — look for date patterns
            target    = cells[1] if len(cells) > 1 else ""
            acquirer  = cells[2] if len(cells) > 2 else ""
            price_str = cells[3] if len(cells) > 3 else ""

            # Find dates (DD-Mon-YYYY or DD/MM/YYYY)
            date_str = ""
            for cell in cells:
                dm = re.search(
                    r'(\d{2}[/-][A-Za-z]{3}[/-]\d{4}|\d{2}[/-]\d{2}[/-]\d{4})',
                    cell
                )
                if dm:
                    raw = dm.group(1)
                    try:
                        for fmt in ("%d-%b-%Y", "%d/%m/%Y", "%d-%m-%Y"):
                            try:
                                date_str = datetime.datetime.strptime(
                                    raw.replace("/", "-"), fmt.replace("/", "-")
                                ).strftime("%Y-%m-%d")
                                break
                            except Exception:
                                continue
                    except Exception:
                        pass
                    if date_str:
                        break

            if not target:
                continue

            # Offer price
            price_m = re.search(r'[\d,]+(?:\.\d+)?', price_str)
            price   = float(price_m.group(0).replace(",", "")) if price_m else None

            # Percentage offered
            pct_str  = cells[4] if len(cells) > 4 else ""
            pct_m    = re.search(r'([\d.]+)\s*%?', pct_str)
            pct      = float(pct_m.group(1)) if pct_m else None

            rows.append({
                "target":   target[:120],
                "acquirer": acquirer[:120],
                "price":    price,
                "pct":      pct,
                "date":     date_str or datetime.date.today().isoformat(),
            })
    except Exception as ex:
        print(f"  [SEBI-OO] Parse error: {ex}")
    return rows


def _sebi_oo_to_result(row: dict, registry: dict) -> dict:
    matched_rec, score = _fuzzy_match(row["target"], registry, threshold=0.55)

    price_str = f"₹{row['price']:,.2f}/share" if row.get("price") else ""
    pct_str   = f"{row['pct']:.1f}% stake" if row.get("pct") else ""
    detail    = " · ".join(filter(None, [price_str, pct_str, f"Acquirer: {row['acquirer'][:60]}"]))

    return {
        "source":         "SEBI-OO",
        "date":           row["date"],
        "company":        row["target"],
        "counterparty":   row["acquirer"],
        "amount_usd_m":   None,
        "detail":         detail,
        "sector":         "",
        "matched_client": matched_rec,
        "match_score":    score,
        "url":            SEBI_OO_URL,
        "fx_angle": (
            "SCB client open offer — acquirer remittance + minority buyout → "
            "large INR→FX or FX→INR flow on offer close"
            if matched_rec else
            "Open offer on listed company — cross-border acquisition → FX conversion on settlement"
        ),
    }


@st.cache_data(ttl=3600 * 6, show_spinner=False)
def scan_sebi_offers(registry: dict) -> dict:
    """
    Scrape SEBI open offer listing and match against registry.
    Falls back to BSE announcement API if SEBI page is unavailable.
    """
    raw_rows  = []
    error_msg = None
    source_used = "SEBI"

    # Primary: BSE announcement API (reliable JSON)
    # SEBI takeover page is JS-rendered — static fetch returns no table data
    try:
        bse_resp = requests.get(
            BSE_OO_URL,
            headers={**HDR, "Referer": "https://www.bseindia.com/"},
            timeout=15,
        )
        data = bse_resp.json()
        source_used = "BSE"
        for item in (data if isinstance(data, list) else data.get("Table", [])):
            company  = item.get("SLONGNAME") or item.get("scrip_name", "")
            acquirer = item.get("HEADLINE", "")[:80]
            date_raw = item.get("News_submission_dt") or item.get("DT_TM", "")
            date_str = date_raw[:10] if date_raw else datetime.date.today().isoformat()
            if company:
                raw_rows.append({
                    "target":   company,
                    "acquirer": acquirer,
                    "price":    None,
                    "pct":      None,
                    "date":     date_str,
                })
        if raw_rows:
            error_msg = None
    except Exception as ex:
        print(f"  [SEBI-OO] BSE primary failed: {ex}")
        error_msg = str(ex)

    # Fallback: SEBI website HTML parse
    if not raw_rows:
        try:
            resp = requests.get(SEBI_OO_URL, headers=HDR, timeout=15, verify=False)
            resp.raise_for_status()
            raw_rows    = _parse_sebi_oo_html(resp.text)
            source_used = "SEBI"
            if raw_rows:
                error_msg = None
        except Exception as ex2:
            print(f"  [SEBI-OO] SEBI fallback also failed: {ex2}")

    results  = [_sebi_oo_to_result(r, registry) for r in raw_rows]
    results.sort(key=lambda x: x["date"], reverse=True)
    matched  = [r for r in results if r["matched_client"] is not None]

    return {
        "results":     results[:50],
        "matched":     matched,
        "fetch_date":  datetime.date.today().isoformat(),
        "total":       len(results),
        "source_used": source_used,
        "error":       error_msg if not results else None,
    }
