"""
storage.py
Persists each day's generated Marathi poem, plus its source headlines, into
poems.json in the repository root.

There is no email/chat delivery in this architecture — the static frontend
(index.html + app.js) fetches poems.json directly once GitHub Pages serves
the repository, and the GitHub Actions workflow commits the updated
poems.json back to the repo after each run so the site auto-updates.

poems.json holds a single JSON array of poem-entry objects, newest first —
each run prepends one new entry so a full 100-day history builds up over
time without the file ever needing to be read/rewritten out of order.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List
from zoneinfo import ZoneInfo

from scraper import NewsArticle

logger = logging.getLogger("storage")

IST = ZoneInfo("Asia/Kolkata")
DEFAULT_POEMS_FILE = Path(__file__).resolve().parent / "poems.json"


class StorageError(Exception):
    """Raised when poems.json cannot be read or written."""


@dataclass
class PoemEntry:
    id: str
    date: str  # ISO date, e.g. "2026-09-19"
    display_date: str  # human-readable, e.g. "19 September 2026"
    poem: str  # the 16-line poem text (stanzas separated by blank lines)
    headlines: List[Dict[str, str]] = field(default_factory=list)  # [{"title": ..., "link": ...}, ...]
    generated_at: str = ""  # ISO 8601 timestamp


def _load_existing(poems_file: Path) -> List[Dict[str, Any]]:
    if not poems_file.exists():
        return []

    try:
        with poems_file.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        raise StorageError(f"Failed to read/parse {poems_file}: {exc}") from exc

    if not isinstance(data, list):
        raise StorageError(f"{poems_file} does not contain a JSON array at the top level.")

    return data


def save_poem_entry(
    poem_text: str,
    articles: List[NewsArticle],
    poems_file: Path = DEFAULT_POEMS_FILE,
) -> PoemEntry:
    """
    Prepend a new poem entry (newest first) to poems.json and rewrite the file.

    `poem_text` is the pure poem (no headline block); `articles` supplies the
    structured source headlines that get stored alongside it.

    Raises StorageError if the existing file is unreadable/malformed, or if
    the updated file cannot be written.
    """
    now = datetime.now(IST)
    entry = PoemEntry(
        id=str(uuid.uuid4()),
        date=now.strftime("%Y-%m-%d"),
        display_date=now.strftime("%d %B %Y"),
        poem=poem_text.strip(),
        headlines=[{"title": article.title, "link": article.link} for article in articles],
        generated_at=now.isoformat(),
    )

    existing = _load_existing(poems_file)
    existing.insert(0, asdict(entry))

    try:
        with poems_file.open("w", encoding="utf-8") as f:
            json.dump(existing, f, ensure_ascii=False, indent=2)
            f.write("\n")
    except OSError as exc:
        raise StorageError(f"Failed to write {poems_file}: {exc}") from exc

    logger.info(
        "Saved poem entry %s (date=%s) to %s. Total entries now: %d",
        entry.id,
        entry.date,
        poems_file,
        len(existing),
    )
    return entry


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    sample_articles = [
        NewsArticle(title="पुणे मेट्रोच्या नव्या मार्गिकेचे उद्घाटन", summary="प्रवाशांसाठी नवी सुविधा सुरू", link="https://www.esakal.com/pune/example-1"),
        NewsArticle(title="पुण्यात पावसाचा जोर वाढला", summary="हवामान खात्याचा इशारा", link="https://www.esakal.com/pune/example-2"),
    ]
    sample_poem = "\n".join(
        [
            "ओळ एक ओळ दोन ओळ तीन ओळ चार",
            "ओळ पाच ओळ सहा ओळ सात ओळ आठ",
            "",
            "ओळ नऊ ओळ दहा ओळ अकरा ओळ बारा",
            "ओळ तेरा ओळ चौदा ओळ पंधरा ओळ सोळा",
        ]
    )

    saved = save_poem_entry(sample_poem, sample_articles, poems_file=Path("poems.test.json"))
    print(f"Saved test entry with id={saved.id} to poems.test.json")
