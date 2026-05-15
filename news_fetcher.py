"""
news_fetcher.py
---------------
Pulls headlines from RSS feeds and NewsAPI.
Returns a clean list of article dictionaries.

Each article looks like:
{
    "title":     "Fed signals further rate hikes ahead",
    "summary":   "The Federal Reserve indicated...",
    "source":    "Reuters Business",
    "published": "2024-06-01 09:30:00",
    "link":      "https://..."
}
"""

import feedparser
import requests
import datetime
import streamlit as st
from config import RSS_FEEDS, NEWS_API_KEY, NEWSAPI_QUERY


def fetch_rss_feeds() -> list:
    """Fetch articles from all configured RSS feeds."""
    articles = []

    for feed_config in RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_config["url"])

            for entry in feed.entries[:25]:   # max 25 per source
                # Clean up the summary — remove HTML tags crudely
                raw_summary = entry.get("summary", "")
                clean_summary = raw_summary.replace("<b>", "").replace("</b>", "") \
                                           .replace("<p>", "").replace("</p>", "") \
                                           .replace("&amp;", "&").replace("&lt;", "<") \
                                           .replace("&gt;", ">")[:300]

                articles.append({
                    "title":     entry.get("title", "No title"),
                    "summary":   clean_summary,
                    "source":    feed_config["name"],
                    "published": entry.get("published", str(datetime.datetime.now())),
                    "link":      entry.get("link", ""),
                })

        except Exception as e:
            # Don't crash the whole dashboard if one feed fails
            print(f"RSS fetch failed for {feed_config['name']}: {e}")

    return articles


def fetch_newsapi() -> list:
    """Fetch articles from NewsAPI (requires free API key)."""
    articles = []

    # If no key is set, skip silently
    if not NEWS_API_KEY:
        return articles

    try:
        url = "https://newsapi.org/v2/everything"
        params = {
            "q":        NEWSAPI_QUERY,
            "language": "en",
            "sortBy":   "publishedAt",
            "pageSize": 20,
            "apiKey":   NEWS_API_KEY,
        }
        response = requests.get(url, params=params, timeout=10)
        data = response.json()

        if data.get("status") == "ok":
            for item in data.get("articles", []):
                articles.append({
                    "title":     item.get("title", "No title"),
                    "summary":   (item.get("description") or "")[:300],
                    "source":    item.get("source", {}).get("name", "NewsAPI"),
                    "published": item.get("publishedAt", ""),
                    "link":      item.get("url", ""),
                })

    except Exception as e:
        print(f"NewsAPI fetch failed: {e}")

    return articles


@st.cache_data(ttl=900)   # Cache for 15 minutes — avoids hammering APIs
def fetch_all_news() -> list:
    """
    Main function to call from the dashboard.
    Fetches from all sources, deduplicates, and returns sorted by recency.
    """
    all_articles = []
    all_articles += fetch_rss_feeds()
    all_articles += fetch_newsapi()

    # Deduplicate by title (same story sometimes appears on multiple feeds)
    seen_titles = set()
    unique_articles = []
    for article in all_articles:
        title_key = article["title"][:60].lower()   # first 60 chars as key
        if title_key not in seen_titles:
            seen_titles.add(title_key)
            unique_articles.append(article)

    return unique_articles
