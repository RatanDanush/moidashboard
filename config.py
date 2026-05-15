"""
config.py
---------
All settings live here. If you want to add a new news source,
new product, or change the refresh interval — this is the only
file you need to touch.
"""

import os
from dotenv import load_dotenv

# Load API keys from your .env file
load_dotenv()

# ─── API KEYS ────────────────────────────────────────────────────────────────
# Get your FREE Groq key at: https://console.groq.com
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")

# Get your FREE NewsAPI key at: https://newsapi.org (100 requests/day free)
NEWS_API_KEY = os.getenv("NEWS_API_KEY", "")

# ─── NEWS SOURCES (RSS) ──────────────────────────────────────────────────────
RSS_FEEDS = [
    {"name": "Reuters Business",    "url": "http://feeds.reuters.com/reuters/businessNews"},
    {"name": "CNBC Top News",       "url": "https://www.cnbc.com/id/100003114/device/rss/rss.html"},
    {"name": "Yahoo Finance",       "url": "https://finance.yahoo.com/news/rss/"},
    {"name": "FT Markets",          "url": "https://www.ft.com/markets?format=rss"},
]

# NewsAPI search query — what topics to scan for
NEWSAPI_QUERY = (
    "forex OR 'interest rates' OR commodities OR 'trade finance' "
    "OR 'emerging markets' OR 'central bank' OR 'oil prices' "
    "OR 'currency' OR 'Fed' OR 'inflation' OR 'supply chain'"
)

# ─── DASHBOARD SETTINGS ──────────────────────────────────────────────────────
REFRESH_INTERVAL_MS = 15 * 60 * 1000   # 15 minutes in milliseconds
MAX_ARTICLES_SHOWN  = 20               # How many headlines in the news feed
MAX_ARTICLES_FOR_AI = 8                # How many headlines to send to AI (keep costs low)

# ─── SC PRODUCT CATALOGUE ────────────────────────────────────────────────────
# These are the products your recommendations will reference.
# Add or edit products here as you learn more from your team.
PRODUCTS = {
    "FX_FORWARD":           "FX Forward",
    "FX_OPTION":            "FX Option",
    "FX_SPOT_PRISMFX":      "FX Spot / SC PrismFX",
    "NDF":                  "Non-Deliverable Forward (NDF)",
    "CROSS_CCY_SWAP":       "Cross-Currency Swap",
    "IRS":                  "Interest Rate Swap",
    "RATE_CAP_FLOOR":       "Rate Cap / Floor",
    "SWAPTION":             "Swaption",
    "COMMODITY_SWAP":       "Commodity Swap",
    "COMMODITY_OPTION":     "Commodity Option / Forward",
    "TRADE_FINANCE":        "Trade Finance / Letter of Credit",
    "SUPPLY_CHAIN_FINANCE": "Supply Chain Finance",
    "STRUCTURED_FX":        "Structured FX Solution",
}

# ─── CLIENT SEGMENTS ─────────────────────────────────────────────────────────
CLIENT_SEGMENTS = {
    "importer":             "Corporate Importer (pays in foreign currency)",
    "exporter":             "Corporate Exporter (receives foreign currency)",
    "floating_borrower":    "Floating-Rate Borrower (exposed to rate rises)",
    "em_usd_borrower":      "EM Corporate with USD/EUR Debt",
    "commodity_producer":   "Commodity Producer (miner, oil company, farmer)",
    "commodity_consumer":   "Commodity Consumer (airline, manufacturer, food company)",
    "financial_institution":"Financial Institution / Bank",
    "paytech":              "PayTech / Fintech (cross-border payments)",
    "asset_manager":        "Asset Manager / Fund",
}
