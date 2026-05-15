"""
ai_recommender.py
-----------------
Sends detected trends + headlines to Groq (free AI API)
and gets back structured product recommendations.

If no Groq key is set, falls back to rule-based recommendations
so the dashboard still works without any API key.
"""

import json
import streamlit as st
from config import GROQ_API_KEY, PRODUCTS, CLIENT_SEGMENTS, MAX_ARTICLES_FOR_AI

# Only import groq if available
try:
    from groq import Groq
    GROQ_AVAILABLE = bool(GROQ_API_KEY)
except ImportError:
    GROQ_AVAILABLE = False


# ─── SYSTEM PROMPT ───────────────────────────────────────────────────────────
# This is the most important thing to get right.
# It tells the AI exactly what role it's playing and what format to return.

SYSTEM_PROMPT = """
You are a senior financial markets salesperson at Standard Chartered Bank.
Your job is to read financial news headlines and recommend specific bank products
to push to clients — including WHAT product, WHO to target, WHY it's timely,
and HOW to pitch it.

Standard Chartered's key products are:
- FX Forward: Lock in exchange rate for future date. For importers/exporters.
- FX Option: Right but not obligation to exchange at set rate. For uncertain flows.
- FX Spot / SC PrismFX: Immediate currency exchange. 130+ currencies including EM.
- Non-Deliverable Forward (NDF): Forward for restricted EM currencies (INR, CNY, PHP etc).
- Cross-Currency Swap: Swap both currency AND interest rate. SC's EM edge — covers KES, NGN, PHP onshore.
- Interest Rate Swap (IRS): Exchange fixed for floating interest payments. For floating-rate borrowers.
- Rate Cap/Floor: Option on interest rates. Cap = ceiling on floating rate cost.
- Swaption: Option to enter a swap later. For uncertain timing of debt issuance.
- Commodity Swap/Option: Hedge energy, metals, agriculture price risk. SC's edge in Asian soft commodities.
- Trade Finance / Letter of Credit: Mitigate counterparty risk in cross-border trade.
- Supply Chain Finance: Accelerate supplier payments, optimise working capital.

SC's key competitive advantage: deepest coverage of emerging market currencies
and on-the-ground presence in Asia, Africa, and the Middle East.

Always return a JSON array — nothing else, no explanation, no markdown.
Each item in the array must have exactly these fields:
- product: string (product name from the list above)
- trend: string (one-line description of the news trend)
- client_segment: string (specific type of client to target)
- urgency: string ("High", "Medium", or "Low")
- why_now: string (why this news makes the client need this product TODAY)
- pitch_angle: string (one sentence the RM says in a client call)
- sc_edge: string (why SC specifically, not a competitor)
"""


def get_ai_recommendations(trends: list, articles: list) -> list:
    """
    Main function called by the dashboard.
    Returns a list of recommendation dicts.
    Falls back to rule-based if no Groq key.
    """
    if not trends:
        return []

    if GROQ_AVAILABLE:
        try:
            return _groq_recommendations(trends, articles)
        except Exception as e:
            print(f"Groq API error: {e}. Falling back to rule-based.")
            return _rule_based_recommendations(trends)
    else:
        return _rule_based_recommendations(trends)


@st.cache_data(ttl=900)   # Cache for 15 min — don't re-call AI every rerun
def _groq_recommendations(trends: list, articles: list) -> list:
    """Call Groq API with the top trends and headlines."""

    client = Groq(api_key=GROQ_API_KEY)

    # Build the user message — summarise trends + top headlines
    top_trends = trends[:5]   # Max 5 trends per call
    top_headlines = [a["title"] for a in articles[:MAX_ARTICLES_FOR_AI]]

    # Format trends clearly for the AI
    trend_summary = "\n".join([
        f"- {t['trend_name'].replace('_', ' ').title()} "
        f"({t['urgency']} urgency, {t['article_count']} articles, "
        f"keywords: {', '.join(t['matched_keywords'][:4])})"
        for t in top_trends
    ])

    headline_summary = "\n".join([f"- {h}" for h in top_headlines])

    user_message = f"""
Today's detected financial trends:
{trend_summary}

Sample headlines driving these trends:
{headline_summary}

Generate product recommendations for Standard Chartered's sales team.
Return ONLY a JSON array with 3-5 recommendations. No other text.
"""

    response = client.chat.completions.create(
        model="llama3-8b-8192",   # Free, fast Llama 3 model on Groq
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": user_message},
        ],
        temperature=0.3,          # Low temperature = more consistent, factual output
        max_tokens=1500,
    )

    raw_text = response.choices[0].message.content.strip()

    # Clean up in case AI wraps in markdown code blocks
    if raw_text.startswith("```"):
        raw_text = raw_text.split("```")[1]
        if raw_text.startswith("json"):
            raw_text = raw_text[4:]

    recommendations = json.loads(raw_text)

    # Validate structure — make sure all required fields exist
    required_fields = ["product", "trend", "client_segment", "urgency",
                       "why_now", "pitch_angle", "sc_edge"]
    valid = []
    for rec in recommendations:
        if all(f in rec for f in required_fields):
            valid.append(rec)

    return valid


def _rule_based_recommendations(trends: list) -> list:
    """
    Fallback when no AI API is available.
    Uses the product hints from trend_detector.py to build
    basic recommendations — no AI required.
    """
    PRODUCT_LABELS = PRODUCTS

    PITCH_TEMPLATES = {
        "currency_volatility":      "Exchange rate volatility is creating FX exposure for your cross-border flows — lock in rates now before it moves further against you.",
        "rate_hike_cycle":          "With rates rising, your floating-rate debt servicing costs are increasing — consider fixing your rate now before the next hike.",
        "rate_cut_cycle":           "As rates fall, locking in a floor protects your investment returns from being eroded further.",
        "em_stress":                "EM currency stress is increasing the real cost of your USD/EUR obligations — a cross-currency swap can eliminate that mismatch entirely.",
        "oil_price_move":           "Oil price volatility is creating margin risk — hedge your exposure now while liquidity is still strong.",
        "metals_move":              "Metal price swings are impacting your input costs — a commodity swap can give you budget certainty for the next 6-12 months.",
        "agriculture_shock":        "Agricultural price volatility is threatening your margins — lock in your input costs before the supply shock flows through.",
        "trade_disruption":         "Supply chain disruption is creating payment and FX risk — trade finance solutions can protect your cross-border cash flows.",
        "geopolitical_risk":        "Geopolitical uncertainty is creating tail-risk FX and commodity exposure — options give you protection while keeping upside.",
        "inflation_pressure":       "Persistent inflation is pushing rates higher — fixing your borrowing costs now protects your P&L.",
        "corporate_debt_issuance":  "If you're issuing debt in a foreign currency, a cross-currency swap eliminates the currency mismatch on your liability.",
    }

    SC_EDGES = {
        "currency_volatility":      "SC trades 130+ currencies including restricted EM ones — no competitor matches our emerging market FX coverage.",
        "rate_hike_cycle":          "SC offers onshore IRS in local EM currencies — fixing your local-currency borrowing costs where others can't.",
        "em_stress":                "SC is the only international bank offering onshore cross-currency swaps in currencies like INR, PHP, KES, NGN.",
        "oil_price_move":           "SC's commodities desk covers Middle East, Africa and Asian energy markets with on-the-ground expertise.",
        "metals_move":              "SC covers Asian mining and smelting clients with local-currency commodity derivatives no global competitor offers.",
        "agriculture_shock":        "SC is the leading bank for palm oil, rubber and rice hedging in Southeast Asia — unmatched local expertise.",
        "trade_disruption":         "SC's trade finance network spans 40+ markets — best-in-class for EM trade corridors.",
        "geopolitical_risk":        "SC's EM footprint means we can still execute when other banks pull back from high-risk markets.",
        "inflation_pressure":       "SC offers rate caps and structured solutions across EM rate markets where vanilla hedging isn't possible.",
        "corporate_debt_issuance":  "SC can execute cross-currency swaps in 130+ currency pairs including restricted EM currencies post-issuance.",
    }

    recommendations = []

    for trend in trends[:5]:
        trend_name = trend["trend_name"]
        product_key = trend["product_hints"][0] if trend["product_hints"] else "FX_FORWARD"
        product_label = PRODUCT_LABELS.get(product_key, product_key)

        recommendations.append({
            "product":        product_label,
            "trend":          trend_name.replace("_", " ").title(),
            "client_segment": trend.get("client_hint", "Corporates with cross-border exposure"),
            "urgency":        trend.get("urgency", "Medium"),
            "why_now":        f"{trend['article_count']} news stories detected on this trend today.",
            "pitch_angle":    PITCH_TEMPLATES.get(trend_name, "Market conditions are creating hedging opportunities — let's discuss your exposure."),
            "sc_edge":        SC_EDGES.get(trend_name, "SC's emerging market coverage and local expertise give us an unmatched edge."),
        })

    return recommendations
