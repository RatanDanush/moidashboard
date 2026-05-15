"""
token_tracker.py
----------------
Tracks daily Groq token consumption against a 100,000 token/day budget.

Budget allocation (updated — briefing removed):
  Web search:      79,000  (signal-triggered + tier fill — ~65 clients/day)
  Classification:   8,000  (8b model, ~50 tokens/item, ~160 headlines/day)
  FX implications:  8,000  (70b model, ~250 tokens/call, ~32 cards/day)
  Deep dive:        5,000  (manual only — on-demand client history)
  ─────────────────────
  Total:          100,000

Resets at midnight UTC (= 5:30am IST).
Stored in token_budget.json on disk.
"""

import json
import os
import datetime

BUDGET_FILE  = "token_budget.json"
DAILY_BUDGET = 100_000

# Per-function allocations
ALLOCATIONS = {
    "web_search":    65_000,   # Groq compound-beta-mini web search per client
    "classify":      20_000,   # was 8k — active search now returns ~500-800 items
    "fx_implication": 8_000,
    "deep_dive":      5_000,
}


# ─── Storage ─────────────────────────────────────────────────────────────────

def _today_utc() -> str:
    return datetime.datetime.utcnow().strftime("%Y-%m-%d")

def load_budget() -> dict:
    """Load budget state from disk. Auto-resets if new UTC day."""
    try:
        if os.path.exists(BUDGET_FILE):
            with open(BUDGET_FILE) as f:
                data = json.load(f)
            if data.get("date") == _today_utc():
                return data
    except Exception:
        pass
    # Fresh day or missing file
    return {
        "date":       _today_utc(),
        "total_used": 0,
        "by_function": {k: 0 for k in ALLOCATIONS},
        "web_search_clients": [],   # list of clients already searched today
        "last_updated": datetime.datetime.utcnow().isoformat(),
    }

def save_budget(data: dict):
    try:
        data["last_updated"] = datetime.datetime.utcnow().isoformat()
        with open(BUDGET_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as ex:
        print(f"  Token tracker save error: {ex}")


# ─── Check + consume ─────────────────────────────────────────────────────────

def can_afford(function: str, estimated_tokens: int) -> bool:
    """Check if we have budget for this call."""
    data       = load_budget()
    alloc      = ALLOCATIONS.get(function, 1000)
    fn_used    = data["by_function"].get(function, 0)
    total_used = data["total_used"]

    return (fn_used + estimated_tokens <= alloc and
            total_used + estimated_tokens <= DAILY_BUDGET)

def record_usage(function: str, tokens_used: int,
                 client_key: str = ""):
    """Record actual token usage after a Groq call."""
    data = load_budget()
    data["total_used"] = data.get("total_used", 0) + tokens_used
    if function not in data["by_function"]:
        data["by_function"][function] = 0
    data["by_function"][function] += tokens_used

    if function == "web_search" and client_key:
        clients = data.get("web_search_clients", [])
        if client_key not in clients:
            clients.append(client_key)
        data["web_search_clients"] = clients

    save_budget(data)

def client_already_searched(client_key: str) -> bool:
    """True if this client already got a Groq web search today."""
    data = load_budget()
    return client_key in data.get("web_search_clients", [])

def remaining(function: str = None) -> int:
    """Return remaining tokens — overall or for a specific function."""
    data = load_budget()
    if function:
        alloc   = ALLOCATIONS.get(function, 0)
        fn_used = data["by_function"].get(function, 0)
        return max(0, alloc - fn_used)
    return max(0, DAILY_BUDGET - data.get("total_used", 0))


# ─── Status for UI ───────────────────────────────────────────────────────────

def get_status() -> dict:
    """Return full status dict for dashboard display."""
    data       = load_budget()
    total_used = data.get("total_used", 0)
    pct        = round(100 * total_used / DAILY_BUDGET)

    # Time until reset (midnight UTC)
    now         = datetime.datetime.utcnow()
    midnight    = (now + datetime.timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0)
    hours_left  = (midnight - now).total_seconds() / 3600

    # IST reset time = UTC midnight = 5:30am IST
    ist_reset   = "5:30 AM IST"

    return {
        "total_used":         total_used,
        "total_budget":       DAILY_BUDGET,
        "pct":                pct,
        "remaining":          DAILY_BUDGET - total_used,
        "by_function":        data.get("by_function", {}),
        "web_search_clients": len(data.get("web_search_clients", [])),
        "hours_until_reset":  round(hours_left, 1),
        "ist_reset":          ist_reset,
        "date":               data.get("date",""),
        "at_limit":           total_used >= DAILY_BUDGET * 0.95,
        "web_search_full":    data["by_function"].get("web_search",0) >= ALLOCATIONS["web_search"] * 0.95,
    }
