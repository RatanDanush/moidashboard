"""
ticker_map.py
-------------
Hand-curated mapping of Indian subsidiary names → NSE ticker symbols.
Used by client_registry.py to enable API-based corporate action lookups.

Format:  "Subsidiary Name (or key part of it)": "NSE_TICKER"

Add/correct tickers here as you learn more.
NSE tickers use the .NS suffix on Yahoo Finance (added automatically in fetchers).
"""

TICKER_MAP = {

    # ── Previously missing listed companies ───────────────────────────────
    # High exposure — $800M–$1.2B
    "Bharti Airtel":                        "BHARTIARTL",
    "Vodafone Idea Ltd":                    "IDEA",
    "Hitachi Energy India":                 "POWERINDIA",
    "GSK India":                            "GLAXO",
    "GlaxoSmithKline Consumer":             "GLAXO",

    # $400M–$800M
    "Blue Dart Express":                    "BLUEDART",
    "GE Power India":                       "GEPIL",
    "GE T&D India":                         "GETD",
    "VE Commercial Vehicles":               "VECV",
    "Heidelberg Materials India":           "HEIDELBERG",

    # $280M–$450M
    "Otis Elevator Company India":          "OTIS",
    "Clariant Chemicals India":             "CLNINDIA",
    "Ingersoll-Rand India":                 "INGERRAND",
    "Merck Life Science":                   "MERCK",
    "Merck India":                          "MERCK",

    # $75M–$180M
    "DIC India Ltd":                        "DICINDIA",
    "Wartsila India":                       "WRTSL",
    "Alstom India Ltd":                     "ALSTOMIND",
    "Sharp India Ltd":                      "SHARPIN",
    "Lotte India Corporation":              "LOTTEIND",
    "Asahi India Glass":                    "ASAHIINDIA",
    "Sembcorp Energy India":                "SEIL",
    "Gland Pharma":                         "GLANDPHARMA",
    "ICRA Ltd":                             "ICRA",
    "Maruti Suzuki India Ltd":              "MARUTI",
    "Hindustan Unilever Ltd":               "HINDUNILVR",
    "Nestle India Ltd":                     "NESTLEIND",
    "Siemens Ltd":                          "SIEMENS",
    "ABB India Ltd":                        "ABB",
    "Bosch Ltd":                            "BOSCHLTD",
    "Schaeffler India Ltd":                 "SCHAEFFLER",
    "Linde India Ltd":                      "LINDEINDIA",
    "Kansai Nerolac Paints Ltd":            "KANSAINER",
    "Akzo Nobel India Ltd":                 "AKZOINDIA",
    "Whirlpool of India Ltd":               "WHIRLPOOL",
    "Castrol India Ltd":                    "CASTROLIND",
    "Pfizer Ltd":                           "PFIZER",
    "Bata India Ltd":                       "BATAINDIA",
    "Reckitt Benckiser India Ltd":          "RECKITTBEN",
    "Bayer CropScience Ltd":                "BAYERCROP",
    "BASF India Ltd":                       "BASF",
    "Procter & Gamble Hygiene":             "PGHH",
    "Novartis India Ltd":                   "NOVARTIND",
    "Hyundai Motor India":                  "HYUNDAI",

    # ── TIER 1 — Other confirmed ───────────────────────────────────────────
    "3M India Ltd":                         "3MINDIA",
    "Honeywell Automation India":           "HONAUT",
    "Cummins India Ltd":                    "CUMMINSIND",
    "SKF India Ltd":                        "SKFINDIA",
    "Timken India Ltd":                     "TIMKEN",
    "Grindwell Norton Ltd":                 "GRINDWELL",
    "Goodyear India Ltd":                   "GOODYEAR",
    "Sanofi India Ltd":                     "SANOFI",
    "Abbott India Ltd":                     "ABBOTINDIA",
    "GlaxoSmithKline Pharmaceuticals":      "GLAXO",
    "Gillette India Ltd":                   "GILLETTE",
    "Colgate Palmolive India":              "COLPAL",
    "Merck Ltd":                            "MERCK",
    "Pfizer Limited":                       "PFIZER",
    "AstraZeneca Pharma India":             "ASTRAZEN",
    "Vesuvius India":                       "VESUVIUS",

    # ── Industrials / Engineering ──────────────────────────────────────────
    "Thermax Ltd":                          "THERMAX",
    "KSB Ltd":                              "KSB",
    "Alfa Laval India Ltd":                 "ALFALAVAL",
    "Ingersoll Rand India":                 "INGERRAND",
    "Elgi Equipments":                      "ELGIEQUIP",
    "Johnson Controls Hitachi":             "JCHAC",
    "Blue Star Ltd":                        "BLUESTAR",

    # ── Auto / Components ──────────────────────────────────────────────────
    "Motherson Sumi":                       "MOTHERSON",
    "Endurance Technologies":               "ENDURANCE",
    "Bharat Forge":                         "BHARATFORG",
    "Sundram Fasteners":                    "SUNDRMFAST",

    # ── FMCG / Consumer ───────────────────────────────────────────────────
    "Britannia Industries":                 "BRITANNIA",
    "Tata Consumer Products":               "TATACONSUM",
    "Marico Ltd":                           "MARICO",
    "Dabur India Ltd":                      "DABUR",
    "Godrej Consumer Products":             "GODREJCP",
    "Emami Ltd":                            "EMAMILTD",
    "United Breweries":                     "UBL",
    "United Spirits":                       "UNITDSPR",

    # ── IT / Tech ─────────────────────────────────────────────────────────
    "Infosys Ltd":                          "INFY",
    "Wipro Ltd":                            "WIPRO",
    "HCL Technologies":                     "HCLTECH",
    "Tech Mahindra":                        "TECHM",
    "Mphasis Ltd":                          "MPHASIS",
    "Oracle Financial Services":            "OFSS",

    # ── Paints / Chemicals ────────────────────────────────────────────────
    "Asian Paints Ltd":                     "ASIANPAINT",
    "Berger Paints India":                  "BERGEPAINT",
    "Atul Ltd":                             "ATUL",
    "Tata Chemicals":                       "TATACHEM",
    "Pidilite Industries":                  "PIDILITIND",
    "SRF Ltd":                              "SRF",

    # ── Diversified / Others ──────────────────────────────────────────────
    "ITC Ltd":                              "ITC",
    "Tata Steel":                           "TATASTEEL",
    "Hindalco Industries":                  "HINDALCO",
    "Vedanta Ltd":                          "VEDL",
    "Adani Wilmar Ltd":                     "AWL",
    "Huhtamaki India Ltd":                  "HUHTAMAKI",
}
