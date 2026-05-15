"""
trend_detector.py
-----------------
Scans article headlines for financial keywords and classifies them
into trends. This runs BEFORE the AI call — it acts as a fast filter
so we only send relevant articles to the AI (saving API quota).

If a headline matches zero keywords, it's skipped.
If it matches keywords, it's tagged with a trend and sent to AI.
"""

# ─── KEYWORD TAXONOMY ────────────────────────────────────────────────────────
# Maps trend names → keywords to look for → initial product hints
# This is your core intellectual contribution to the project.
# Discuss and refine these mappings with your manager/team.

TREND_TAXONOMY = {

    "currency_volatility": {
        "keywords": [
            "currency", "forex", "fx", "exchange rate", "weakening", "strengthening",
            "depreciation", "appreciation", "dollar surge", "dollar strength",
            "rupee", "yuan", "renminbi", "peso", "ringgit", "rupiah",
            "currency risk", "devaluation", "revaluation"
        ],
        "product_hints": ["FX_FORWARD", "FX_OPTION", "FX_SPOT_PRISMFX", "NDF"],
        "urgency": "High",
        "client_hint": "importers and exporters",
    },

    "rate_hike_cycle": {
        "keywords": [
            "rate hike", "interest rate", "fed raises", "hawkish", "tightening",
            "basis points", "federal reserve", "rbi", "monetary policy",
            "rate increase", "rate decision", "central bank", "policy rate",
            "quantitative tightening", "qt", "yield curve"
        ],
        "product_hints": ["IRS", "RATE_CAP_FLOOR", "SWAPTION"],
        "urgency": "High",
        "client_hint": "floating-rate borrowers and banks",
    },

    "rate_cut_cycle": {
        "keywords": [
            "rate cut", "dovish", "easing", "rate reduction", "pivot",
            "cutting rates", "lower rates", "loosening monetary policy",
            "quantitative easing", "qe", "stimulus"
        ],
        "product_hints": ["IRS", "RATE_CAP_FLOOR"],
        "urgency": "Medium",
        "client_hint": "fixed-rate lenders and asset managers",
    },

    "em_stress": {
        "keywords": [
            "emerging market", "em selloff", "capital outflow", "em currency",
            "developing market", "frontier market", "balance of payments",
            "imf bailout", "sovereign debt", "debt distress", "contagion",
            "india", "china", "indonesia", "nigeria", "kenya", "vietnam",
            "bangladesh", "pakistan", "egypt", "ghana"
        ],
        "product_hints": ["CROSS_CCY_SWAP", "NDF", "FX_FORWARD", "STRUCTURED_FX"],
        "urgency": "High",
        "client_hint": "EM corporates with USD debt",
    },

    "oil_price_move": {
        "keywords": [
            "oil price", "crude oil", "brent", "wti", "opec", "opec+",
            "oil supply", "oil demand", "petroleum", "energy price",
            "oil spike", "oil crash", "oil rally", "barrel", "jet fuel",
            "oil output", "oil production cut", "saudi arabia oil"
        ],
        "product_hints": ["COMMODITY_SWAP", "COMMODITY_OPTION"],
        "urgency": "High",
        "client_hint": "airlines, oil producers, energy companies",
    },

    "metals_move": {
        "keywords": [
            "gold price", "copper", "aluminium", "aluminum", "nickel",
            "iron ore", "zinc", "precious metals", "base metals",
            "mining", "lme", "comex", "gold rally", "metal prices",
            "lithium", "cobalt", "ev metals", "battery metals"
        ],
        "product_hints": ["COMMODITY_SWAP", "COMMODITY_OPTION"],
        "urgency": "Medium",
        "client_hint": "miners, manufacturers, EV battery producers",
    },

    "agriculture_shock": {
        "keywords": [
            "palm oil", "rubber", "rice price", "wheat", "corn", "soybean",
            "crop", "harvest", "el nino", "la nina", "drought", "flood",
            "food inflation", "agricultural", "sugar price", "coffee price",
            "cocoa", "soft commodities", "fertilizer", "food supply"
        ],
        "product_hints": ["COMMODITY_SWAP", "COMMODITY_OPTION"],
        "urgency": "Medium",
        "client_hint": "agri producers, food manufacturers, trading companies",
    },

    "trade_disruption": {
        "keywords": [
            "tariff", "trade war", "sanctions", "supply chain", "trade disruption",
            "export ban", "import restriction", "trade deal", "fta",
            "logistics", "shipping", "freight", "port congestion",
            "reshoring", "nearshoring", "supply chain restructuring"
        ],
        "product_hints": ["TRADE_FINANCE", "SUPPLY_CHAIN_FINANCE", "FX_FORWARD", "FX_SPOT_PRISMFX"],
        "urgency": "Medium",
        "client_hint": "importers, exporters, MNCs with global supply chains",
    },

    "geopolitical_risk": {
        "keywords": [
            "war", "conflict", "invasion", "sanctions", "geopolitical",
            "tension", "crisis", "political risk", "instability",
            "coup", "election", "protest", "unrest", "missile",
            "middle east", "russia", "ukraine", "taiwan", "south china sea"
        ],
        "product_hints": ["FX_OPTION", "COMMODITY_OPTION", "STRUCTURED_FX", "TRADE_FINANCE"],
        "urgency": "High",
        "client_hint": "all client types with EM or cross-border exposure",
    },

    "inflation_pressure": {
        "keywords": [
            "inflation", "cpi", "pce", "price pressure", "cost of living",
            "inflationary", "price surge", "hyperinflation", "stagflation",
            "producer prices", "ppi", "wage growth", "price index"
        ],
        "product_hints": ["IRS", "RATE_CAP_FLOOR", "COMMODITY_SWAP"],
        "urgency": "Medium",
        "client_hint": "borrowers, commodity consumers, corporates with fixed costs",
    },

    "corporate_debt_issuance": {
        "keywords": [
            "bond issuance", "debt offering", "raises capital", "bond sale",
            "loan syndication", "refinancing", "leverage buyout", "lbo",
            "acquisition finance", "project finance", "infrastructure bond",
            "green bond", "sukuk"
        ],
        "product_hints": ["CROSS_CCY_SWAP", "IRS", "SWAPTION", "FX_FORWARD"],
        "urgency": "Medium",
        "client_hint": "corporates and financial institutions issuing debt",
    },
}


def detect_trends(articles: list) -> list:
    """
    Scan each article headline + summary for keywords.
    Returns a list of detected trend objects, sorted by urgency.

    Each detected trend looks like:
    {
        "trend_name": "oil_price_move",
        "urgency": "High",
        "product_hints": ["COMMODITY_SWAP", "COMMODITY_OPTION"],
        "client_hint": "airlines, oil producers, energy companies",
        "matching_headlines": ["Oil surges to $95 on OPEC cuts", ...],
        "article_count": 3
    }
    """
    # Count how many articles match each trend
    trend_matches = {}

    for article in articles:
        # Combine title + summary into one searchable text block
        text = (article.get("title", "") + " " + article.get("summary", "")).lower()

        for trend_name, trend_config in TREND_TAXONOMY.items():
            matched_keywords = [kw for kw in trend_config["keywords"] if kw.lower() in text]

            if matched_keywords:
                if trend_name not in trend_matches:
                    trend_matches[trend_name] = {
                        "trend_name":        trend_name,
                        "urgency":           trend_config["urgency"],
                        "product_hints":     trend_config["product_hints"],
                        "client_hint":       trend_config["client_hint"],
                        "matching_headlines": [],
                        "matched_keywords":  set(),
                        "article_count":     0,
                    }
                trend_matches[trend_name]["matching_headlines"].append(article["title"])
                trend_matches[trend_name]["matched_keywords"].update(matched_keywords)
                trend_matches[trend_name]["article_count"] += 1

    # Convert sets to lists for JSON serialisation
    results = []
    for trend in trend_matches.values():
        trend["matched_keywords"] = list(trend["matched_keywords"])
        results.append(trend)

    # Sort: High urgency first, then by article count
    urgency_order = {"High": 0, "Medium": 1, "Low": 2}
    results.sort(key=lambda x: (urgency_order.get(x["urgency"], 2), -x["article_count"]))

    return results
