"""
main.py
Orchestrates the full daily pipeline:

    1. Scrape top Pune news headlines (scraper.py)
    2. Generate a 16-line Marathi poem from them (poem_generator.py)
    3. Prepend the poem + source headlines to poems.json (storage.py)

Designed to run non-interactively inside GitHub Actions. After this script
exits 0, the workflow's own git step commits and pushes the updated
poems.json back to the repository — GitHub Pages then serves the refreshed
poems.json to the static frontend, so the site "auto-updates" without any
push/notification logic living in Python.

Every stage is wrapped so that a failure produces a clear, loggable error
and a non-zero exit code, which makes the GitHub Actions run show as failed
(visible in the Actions tab, and emailed to you if you have GitHub's
"Actions" notification setting enabled).
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from poem_generator import PoemGenerationError, generate_marathi_poem
from scraper import NewsScrapeError, fetch_pune_news
from storage import StorageError, save_poem_entry

IST = ZoneInfo("Asia/Kolkata")

REQUIRED_ENV_VARS = ["GEMINI_API_KEY"]


def _configure_logging() -> logging.Logger:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    return logging.getLogger("main")


def _load_config(logger: logging.Logger) -> dict:
    # Locally, values come from a .env file. In GitHub Actions, they are
    # injected as real environment variables from repository secrets, so
    # load_dotenv() is a harmless no-op there (no .env file present).
    load_dotenv()

    config = {name: os.environ.get(name) for name in REQUIRED_ENV_VARS}
    missing = [name for name, value in config.items() if not value]
    if missing:
        logger.critical("Missing required environment variables: %s", ", ".join(missing))
        sys.exit(1)

    return config


def run_pipeline() -> int:
    logger = _configure_logging()
    started_at = datetime.now(IST)
    logger.info("=== Daily Pune news-poem pipeline started at %s IST ===", started_at.strftime("%Y-%m-%d %H:%M:%S"))

    config = _load_config(logger)

    # --- Step 1: Scrape news -------------------------------------------------
    logger.info("Step 1/3: Fetching top Pune news from Sakal...")
    try:
        articles = fetch_pune_news(max_articles=5)
        logger.info("Fetched %d articles:", len(articles))
        for i, article in enumerate(articles, start=1):
            logger.info("  %d. %s", i, article.title)
    except NewsScrapeError as exc:
        logger.critical("News scraping failed: %s", exc)
        return 1

    # --- Step 2: Generate poem ------------------------------------------------
    logger.info("Step 2/3: Generating Marathi poem with Gemini 1.5 Flash...")
    try:
        poem_text = generate_marathi_poem(articles, config["GEMINI_API_KEY"])
        logger.info("Poem generated successfully (%d characters).", len(poem_text))
    except PoemGenerationError as exc:
        logger.critical("Poem generation failed: %s", exc)
        return 1

    # --- Step 3: Persist to poems.json -----------------------------------------
    logger.info("Step 3/3: Saving poem + headlines to poems.json...")
    try:
        entry = save_poem_entry(poem_text, articles)
        logger.info("Saved poem entry id=%s date=%s", entry.id, entry.date)
    except StorageError as exc:
        logger.critical("Saving to poems.json failed: %s", exc)
        return 1

    finished_at = datetime.now(IST)
    duration = (finished_at - started_at).total_seconds()
    logger.info("=== Pipeline completed successfully in %.1f seconds ===", duration)
    return 0


if __name__ == "__main__":
    sys.exit(run_pipeline())
