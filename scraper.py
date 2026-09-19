"""
scraper.py
Scrapes the top local news stories for the Sakal Pune edition (esakal.com).

Strategy (in order, first one that returns >= MIN_ARTICLES wins):
  1. RSS feed(s) published by esakal.com for the Pune section.
  2. Direct HTML scrape of https://www.esakal.com/pune using primary CSS selectors.
  3. Direct HTML scrape using a generic "any link that looks like an article" fallback.

Every network call has a timeout and a retry-with-backoff so a slow/flaky site
does not hang the GitHub Actions job. If every strategy fails, NewsScrapeError
is raised so the caller (main.py) can decide how to handle a total failure
(e.g. send a "scrape failed" alert email instead of silently doing nothing).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import List, Optional

import feedparser
import requests
from bs4 import BeautifulSoup

logger = logging.getLogger("scraper")

PUNE_PAGE_URL = "https://www.esakal.com/pune"

# esakal.com publishes per-section RSS feeds. We try a few plausible paths
# because Sakal has changed its RSS URL scheme in the past; the first feed
# that parses successfully and yields entries is used.
RSS_FEED_CANDIDATES = [
    "https://www.esakal.com/rssfeed/pune",
    "https://www.esakal.com/pune/rss",
    "https://www.esakal.com/rss/pune.xml",
    "https://www.esakal.com/feed/pune",
]

MIN_ARTICLES = 3
MAX_ARTICLES_DEFAULT = 5
REQUEST_TIMEOUT_SECONDS = 15
MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 2

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "mr,en-IN;q=0.9,en;q=0.8",
}


class NewsScrapeError(Exception):
    """Raised when every scraping strategy fails to produce enough articles."""


@dataclass
class NewsArticle:
    title: str
    summary: str = ""
    link: str = ""

    def __post_init__(self) -> None:
        self.title = " ".join(self.title.split())
        self.summary = " ".join(self.summary.split())

    def as_text(self) -> str:
        if self.summary:
            return f"{self.title} — {self.summary}"
        return self.title


def _get_with_retries(url: str) -> Optional[requests.Response]:
    """GET a URL with retries + exponential backoff. Returns None on total failure."""
    last_error: Optional[Exception] = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT_SECONDS)
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            last_error = exc
            logger.warning("Attempt %d/%d failed for %s: %s", attempt, MAX_RETRIES, url, exc)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SECONDS * attempt)
    logger.error("All %d attempts failed for %s (%s)", MAX_RETRIES, url, last_error)
    return None


def _clean_html_fragment(html_fragment: str) -> str:
    return BeautifulSoup(html_fragment, "lxml").get_text(" ", strip=True)


def _try_rss(max_articles: int) -> List[NewsArticle]:
    for feed_url in RSS_FEED_CANDIDATES:
        logger.info("Trying RSS feed: %s", feed_url)
        response = _get_with_retries(feed_url)
        if response is None:
            continue
        try:
            parsed = feedparser.parse(response.content)
        except Exception as exc:  # feedparser rarely raises, but be defensive
            logger.warning("Failed to parse RSS feed %s: %s", feed_url, exc)
            continue

        if not parsed.entries:
            logger.info("RSS feed %s parsed but had no entries", feed_url)
            continue

        articles: List[NewsArticle] = []
        for entry in parsed.entries[:max_articles]:
            title = entry.get("title", "").strip()
            if not title:
                continue
            raw_summary = entry.get("summary", "") or entry.get("description", "")
            summary = _clean_html_fragment(raw_summary) if raw_summary else ""
            link = entry.get("link", "")
            articles.append(NewsArticle(title=title, summary=summary, link=link))

        if len(articles) >= MIN_ARTICLES:
            logger.info("RSS feed %s yielded %d articles", feed_url, len(articles))
            return articles
        logger.info("RSS feed %s only yielded %d articles (need %d)", feed_url, len(articles), MIN_ARTICLES)

    return []


# Primary selectors target Sakal's card-based article listing layout.
# These are kept as a list of (container_selector, title_selector, summary_selector)
# tuples so we can try several known layouts without rewriting scraping logic.
PRIMARY_SELECTOR_SETS = [
    {
        "container": "div.article-list article",
        "title": "h2, h3",
        "summary": "p",
        "link": "a",
    },
    {
        "container": "div.card-content",
        "title": "h2 a, h3 a, a.title",
        "summary": "p",
        "link": "a",
    },
    {
        "container": "li.article-box",
        "title": "h2, h3, a",
        "summary": "p",
        "link": "a",
    },
]


def _absolutize(link: str) -> str:
    if not link:
        return ""
    if link.startswith("http"):
        return link
    if link.startswith("/"):
        return f"https://www.esakal.com{link}"
    return link


def _try_html_primary(max_articles: int) -> List[NewsArticle]:
    response = _get_with_retries(PUNE_PAGE_URL)
    if response is None:
        return []

    soup = BeautifulSoup(response.text, "lxml")

    for selector_set in PRIMARY_SELECTOR_SETS:
        containers = soup.select(selector_set["container"])
        if not containers:
            continue

        articles: List[NewsArticle] = []
        for container in containers:
            title_el = container.select_one(selector_set["title"])
            if not title_el:
                continue
            title = title_el.get_text(" ", strip=True)
            if not title:
                continue

            summary_el = container.select_one(selector_set["summary"])
            summary = summary_el.get_text(" ", strip=True) if summary_el else ""

            link_el = container.select_one(selector_set["link"])
            link = _absolutize(link_el.get("href", "")) if link_el else ""

            articles.append(NewsArticle(title=title, summary=summary, link=link))
            if len(articles) >= max_articles:
                break

        if len(articles) >= MIN_ARTICLES:
            logger.info(
                "HTML primary selector set %s yielded %d articles",
                selector_set["container"],
                len(articles),
            )
            return articles

    return []


def _try_html_generic_fallback(max_articles: int) -> List[NewsArticle]:
    """
    Last-resort strategy: grab every <a> tag on the Pune page whose visible text
    looks like a real headline (long enough, not nav/menu boilerplate) and whose
    href looks like an article permalink.
    """
    response = _get_with_retries(PUNE_PAGE_URL)
    if response is None:
        return []

    soup = BeautifulSoup(response.text, "lxml")
    seen_titles = set()
    articles: List[NewsArticle] = []

    for anchor in soup.find_all("a", href=True):
        text = anchor.get_text(" ", strip=True)
        href = anchor["href"]

        if len(text) < 25 or len(text) > 200:
            continue
        if "/pune" not in href and "esakal.com" not in href and not href.startswith("/"):
            continue
        # Skip obvious navigation / category links (they tend to be short slugs).
        if text.lower() in seen_titles:
            continue
        seen_titles.add(text.lower())

        articles.append(NewsArticle(title=text, summary="", link=_absolutize(href)))
        if len(articles) >= max_articles:
            break

    if len(articles) >= MIN_ARTICLES:
        logger.info("Generic HTML fallback yielded %d articles", len(articles))
        return articles

    return []


def fetch_pune_news(max_articles: int = MAX_ARTICLES_DEFAULT) -> List[NewsArticle]:
    """
    Fetch the top `max_articles` local news stories for Sakal's Pune edition.

    Tries RSS first, then two tiers of HTML scraping. Raises NewsScrapeError
    if every strategy fails to produce at least MIN_ARTICLES articles.
    """
    strategies = [
        ("RSS feed", _try_rss),
        ("HTML primary selectors", _try_html_primary),
        ("HTML generic fallback", _try_html_generic_fallback),
    ]

    for name, strategy_fn in strategies:
        logger.info("Attempting news source strategy: %s", name)
        try:
            articles = strategy_fn(max_articles)
        except Exception as exc:  # never let one bad strategy kill the whole run
            logger.exception("Strategy '%s' raised an unexpected error: %s", name, exc)
            continue

        if articles:
            return articles[:max_articles]

    raise NewsScrapeError(
        "All scraping strategies (RSS + HTML primary + HTML fallback) failed to "
        "retrieve at least %d Pune news articles." % MIN_ARTICLES
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    news = fetch_pune_news()
    for i, article in enumerate(news, start=1):
        print(f"{i}. {article.title}")
        if article.summary:
            print(f"   {article.summary}")
        if article.link:
            print(f"   {article.link}")
