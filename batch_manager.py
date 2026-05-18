"""
batch_manager.py  v3
--------------------
Gemini-powered batch search with two-model routing.

Priority order:
  P1 — Signal-triggered: RSS/Google News found M&A/FDI/Strategic/IPO/Buyback
       for this client this refresh → heavy search regardless of tier
  P2 — Tier 1 fill: high-value clients with no RSS signal today
  P3 — Tier 2 fill: lite search
  P4 — Everyone else: lite search

Model routing (respects free-tier RPD limits):
  Heavy (P1 + top Tier 1, max 20/day) → gemini-2.5-flash  (20 RPD / 5 RPM / 12s sleep)
  Lite  (P2 remainder + P3 + P4)      → gemini-3.1-flash-lite (500 RPD / 15 RPM / 6s sleep)

Daily capacity: 20 high-quality + 480 lite = true 390-client coverage.
"""

import json, os, datetime, time
import streamlit as st

CACHE_FILE          = "web_search_cache.json"
BATCH_COOLDOWN_MINS = 30   # matches 30-min autorefresh — prevents parallel runs from multiple sessions


# ─── Cache I/O ────────────────────────────────────────────────────────────────

def load_cache() -> dict:
    try:
        if os.path.exists(CACHE_FILE):
            with open(CACHE_FILE) as f:
                return json.load(f)
    except Exception as ex:
        print(f"  Cache load error: {ex}")
    return {}

def save_cache(data: dict):
    try:
        with open(CACHE_FILE, "w") as f:
            json.dump(data, f, indent=2, default=str)
    except Exception as ex:
        print(f"  Cache save error: {ex}")


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _client_key(rec: dict) -> str:
    return f"{rec.get('client_group','')}|{rec.get('indian_subsidiary','')}"

def _hours_since(iso_str: str) -> float:
    try:
        then = datetime.datetime.fromisoformat(iso_str)
        return (datetime.datetime.now() - then).total_seconds() / 3600
    except Exception:
        return 9999.0

def should_run_batch(cache: dict) -> bool:
    last = cache.get("_meta", {}).get("last_batch_run")
    if not last:
        return True
    return _hours_since(last) * 60 >= BATCH_COOLDOWN_MINS

def mark_batch_run(cache: dict) -> dict:
    if "_meta" not in cache:
        cache["_meta"] = {}
    cache["_meta"]["last_batch_run"] = datetime.datetime.now().isoformat()
    return cache

def mark_searched(cache: dict, rec: dict, events: list) -> dict:
    key = _client_key(rec)
    cache[key] = {
        "last_searched":  datetime.datetime.now().isoformat(),
        "events":         events,
        "client_group":   rec.get("client_group",""),
        "subsidiary":     rec.get("indian_subsidiary",""),
    }
    return cache


# ─── Priority queue builder ───────────────────────────────────────────────────

def build_priority_queue(registry: dict,
                         signal_clients: set,
                         cache: dict) -> list:
    """
    Build ordered list of clients to Gemini web search.

    signal_clients: set of client_keys where RSS/GNews found
                    M&A/FDI/Strategic/IPO/Buyback this refresh

    Priority:
      P1 — in signal_clients (any tier)
      P2 — Tier 1, not in signal_clients, not searched today
      P3 — Tier 2, not in signal_clients, not searched today
      P4 — everyone else (skipped — RSS only)

    Dedup: checks BOTH token_tracker (within-session) AND web_search_cache.json
    (cross-session, updated after each client). Prevents parallel sessions from
    searching the same client and burning duplicate RPD budget.
    """
    from token_tracker import client_already_searched

    all_recs = [r for r in registry["all"]
                if r.get("indian_subsidiary") and r.get("client_group")]

    p1, p2, p3 = [], [], []

    for rec in all_recs:
        key = _client_key(rec)

        # Skip if already searched today — check BOTH sources:
        # token_tracker: fast in-memory dedup for current session
        # cache: cross-session dedup (shared file, updated after each client)
        if client_already_searched(key):
            continue
        if _hours_since(cache.get(key, {}).get("last_searched", "")) < 23:
            continue   # another session already searched this client today

        tier = rec.get("priority_tier","") or ""

        if key in signal_clients:
            p1.append(rec)
        elif "TIER 1" in tier.upper():
            p2.append(rec)
        elif "TIER 2" in tier.upper():
            p3.append(rec)

    # Sort within each priority by NIH exposure desc
    for lst in [p1, p2, p3]:
        lst.sort(key=lambda r: r.get("net_nih_exposure",0) or 0, reverse=True)

    return p1 + p2 + p3


# ─── Main runner ──────────────────────────────────────────────────────────────

def run_next_batch(registry: dict,
                   signal_clients: set = None) -> dict:
    """
    Runs after main feed loads — non-blocking background intelligence pass.

    Two-model routing:
      Heavy (≤20 calls/day) → gemini-2.5-flash,       12s sleep
      Lite  (≤500 calls/day) → gemini-3.1-flash-lite,   6s sleep

    Race condition protection:
      Random jitter (0-10s) before cooldown check staggers simultaneous
      session starts. Combined with session_state guard in app.py, prevents
      multiple sessions from all passing should_run_batch() simultaneously.
    """
    import random
    from gemini_engine import (web_search_clients_batch, web_search_clients_batch_lite,
                               _get_heavy_key, GEMINI_API_KEY)
    from token_tracker import get_status

    if not GEMINI_API_KEY:
        return load_cache()

    # Jitter: stagger simultaneous session starts so first writer wins
    time.sleep(random.uniform(0, 10))

    cache = load_cache()

    if not should_run_batch(cache):
        print("  Batch: cooldown active, skipping")
        return cache

    if signal_clients is None:
        signal_clients = set()

    queue = build_priority_queue(registry, signal_clients, cache)

    if not queue:
        print("  Batch: nothing to search")
        return cache

    # ── Route: batch heavy → 2.5 Flash, batch lite → 3.1 Flash Lite ─────────
    # Batching N clients per API call trades RPM for TPM:
    #   Free tier: 5 RPM / 250K TPM → batching is always the right trade.
    # BATCH_HEAVY=2: 20 RPD × 2 clients × n_keys = n_keys×40 clients/day (high quality)
    # BATCH_LITE=3:  500 RPD × 3 clients × n_keys = n_keys×1500 clients/day (coverage)
    from gemini_engine import n_heavy_keys
    BATCH_HEAVY = 2
    BATCH_LITE  = 3
    _n_keys     = max(1, n_heavy_keys())
    HEAVY_LIMIT = 20 * _n_keys * BATCH_HEAVY   # clients (not calls) before switching to lite
    HEAVY_SLEEP = max(4, 12 // _n_keys)        # per API call; round-robin keeps per-key RPM ≤ 5

    heavy_queue = queue[:HEAVY_LIMIT]
    lite_queue  = queue[HEAVY_LIMIT:]

    print(f"  Batch: {len(queue)} clients queued — "
          f"{len(heavy_queue)} heavy (2.5 Flash ×{_n_keys}keys, {BATCH_HEAVY}/call, {HEAVY_SLEEP}s) / "
          f"{len(lite_queue)} lite (3.1 Lite, {BATCH_LITE}/call, 6s) | "
          f"{len(signal_clients)} signal-triggered")
    cache = mark_batch_run(cache)

    searched = 0

    # ── Heavy pass — 2 clients per call, round-robin across keys ─────────────
    for i in range(0, len(heavy_queue), BATCH_HEAVY):
        batch = heavy_queue[i:i + BATCH_HEAVY]
        api_key, key_id = _get_heavy_key()
        if not api_key:
            print(f"  Batch: all heavy keys exhausted — switching to lite.")
            save_cache(cache)
            break
        try:
            all_events = web_search_clients_batch(batch, api_key=api_key, key_id=key_id)
            for rec, events in zip(batch, all_events):
                cache = mark_searched(cache, rec, events)
                sub   = rec.get("indian_subsidiary", "")
                print(f"    ✓[2.5F×{BATCH_HEAVY}] {sub[:30]:<30} {len(events)} events")
                searched += 1
            time.sleep(HEAVY_SLEEP)
        except Exception as ex:
            err = str(ex)
            if "rate limit" in err.lower():
                print(f"  Batch: 2.5 Flash rate limit — saving and switching to lite.")
                save_cache(cache)
                break
            names = [r.get("indian_subsidiary","")[:15] for r in batch]
            print(f"    ✗[2.5F×{BATCH_HEAVY}] {names} — {err[:50]}")
            for rec in batch:
                cache = mark_searched(cache, rec, [])

    # ── Lite pass — 3 clients per call, round-robin across keys ──────────────
    for i in range(0, len(lite_queue), BATCH_LITE):
        batch = lite_queue[i:i + BATCH_LITE]
        try:
            all_events = web_search_clients_batch_lite(batch)
            for rec, events in zip(batch, all_events):
                cache = mark_searched(cache, rec, events)
                sub   = rec.get("indian_subsidiary", "")
                print(f"    ✓[3.1L×{BATCH_LITE}] {sub[:30]:<30} {len(events)} events")
                searched += 1
            time.sleep(6)
        except Exception as ex:
            err = str(ex)
            if "rate limit" in err.lower():
                print(f"  Batch: Lite rate limit — pausing. {searched} done this cycle.")
                save_cache(cache)
                return cache
            names = [r.get("indian_subsidiary","")[:15] for r in batch]
            print(f"    ✗[3.1L×{BATCH_LITE}] {names} — {err[:50]}")
            for rec in batch:
                cache = mark_searched(cache, rec, [])

    save_cache(cache)
    print(f"  Batch complete: {searched} clients searched this cycle")
    return cache


# ─── Progress + cached events ────────────────────────────────────────────────

def get_progress(registry: dict, cache: dict) -> dict:
    from token_tracker import get_status
    tok = get_status()

    all_recs = [r for r in registry["all"]
                if r.get("indian_subsidiary") and r.get("client_group")]
    total    = len(all_recs)
    fresh    = sum(1 for r in all_recs
                   if _hours_since(cache.get(_client_key(r),{})
                                   .get("last_searched","")) < 24)

    last_run  = cache.get("_meta",{}).get("last_batch_run","")
    mins_ago  = int(_hours_since(last_run) * 60) if last_run else None
    next_in   = max(0, BATCH_COOLDOWN_MINS - (mins_ago or BATCH_COOLDOWN_MINS))

    return {
        "total":             total,
        "fresh_24h":         fresh,
        "pct":               round(100 * fresh / total) if total else 0,
        "next_in_mins":      round(next_in, 1),
        "token_used":        tok["total_used"],
        "token_budget":      tok["total_budget"],
        "token_pct":         tok["pct"],
        "token_web_clients": tok["web_search_clients"],
        "token_at_limit":    tok["at_limit"],
        "token_web_full":    tok["web_search_full"],
        "token_reset":       tok["ist_reset"],
        "hours_to_reset":    tok["hours_until_reset"],
    }

def get_all_cached_events(registry: dict, cache: dict) -> list:
    by_key = {_client_key(r): r for r in registry["all"]}
    out    = []
    for key, entry in cache.items():
        if key == "_meta": continue
        rec    = by_key.get(key)
        events = entry.get("events", [])
        for ev in events:
            out.append({
                "company_name":   entry.get("subsidiary","")[:60],
                "ticker":         rec.get("ticker") if rec else None,
                "action_type":    ev.get("action_type","Other"),
                "headline":       ev.get("headline","")[:200],
                "date":           str(ev.get("date",""))[:10],
                "amount":         ev.get("amount"),
                "currency":       ev.get("currency","USD"),
                "source":         "Gemini web search",
                "raw_detail":     ev.get("fx_implication",
                                         ev.get("raw_detail",""))[:300],
                "url":            ev.get("url","") or "",
                "foreign_entity": ev.get("counterparty",
                                         ev.get("foreign_entity")),
                "_significance":  ev.get("significance","Medium"),
                "_pre_matched":   rec,
                # ── Pre-populated classification flags ───────────────────────
                # Gemini already verified INR relevance and filtered noise.
                # These prevent redundant Groq reclassification in corporate_fetcher,
                # which would waste tokens and risk downgrading 2.5 Flash judgement
                # with the weaker 8b classifier.
                "_inr_involved":       True,
                "_is_primary_subject": True,
                "_groq_significant":   True,
                "_groq_confidence":    "high",
                "_skip_india_india":   False,
                "_indian_sub_div":     ev.get("action_type","") == "Dividend",
                "_gemini_verified":    True,
            })
    print(f"  Cache: {len(out)} events from {len(cache)-1} searched clients")
    return out
