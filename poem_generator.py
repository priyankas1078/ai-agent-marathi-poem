"""
poem_generator.py
Turns a list of scraped Pune news headlines into a 16-line (4 stanzas x 4 lines)
rhythmic, rhyming Marathi poem using Google Gemini (see MODEL_NAME) via the
free tier of Google AI Studio.

Two failure modes are handled explicitly, since both were observed in
production runs against the free tier:

  - Reasoning/scratchpad leakage: the model sometimes emits chain-of-thought,
    drafting notes, or topic-analysis text ("*Drafting lines:* Topic 4: ...")
    alongside or instead of the poem. Naive `len(lines) == 16` validation on
    the raw response fails outright on this. This module instead runs every
    line through a cleaning + filtering pipeline that strips markdown
    artifacts, discards any line containing English letters or known
    reasoning-leak keywords (in English or common Devanagari transliteration),
    and keeps only lines with real Devanagari content — then validates line
    count against the *cleaned* output.

  - 429 rate limiting: the free tier allows only 5 requests/minute. A fixed
    20s pause between every retry keeps the pipeline at ~3 requests/minute,
    comfortably under that limit.

The daily job must never crash over formatting: attempts 1-3 require exactly
16 cleaned lines, but the final attempt relaxes to accepting 8-24 cleaned
lines, and failing that, returns the best cleaned output seen across every
attempt rather than raising. PoemGenerationError is only raised if no
attempt ever produced any usable Devanagari text at all.
"""

from __future__ import annotations

import logging
import os
import re
import time
from typing import List

import google.generativeai as genai
from google.api_core.exceptions import ResourceExhausted

from scraper import NewsArticle

logger = logging.getLogger("poem_generator")

MODEL_NAME = os.environ.get("GEMINI_MODEL_NAME", "gemini-3.6-flash")

EXPECTED_STANZAS = 4
LINES_PER_STANZA = 4
EXPECTED_LINES = EXPECTED_STANZAS * LINES_PER_STANZA
MAX_GENERATION_ATTEMPTS = 4

# Relaxed acceptance range used only on the final attempt, so a slightly
# malformed poem still gets published instead of crashing the daily job.
MIN_FALLBACK_LINES = 8
MAX_FALLBACK_LINES = 24

# Free tier is 5 requests/minute; a flat 20s gap between attempts keeps us
# at ~3 requests/minute, safely under that ceiling.
RETRY_DELAY_SECONDS = 20

SYSTEM_INSTRUCTION = (
    "You are a Marathi poet. Output ONLY the Marathi poem. Never output your "
    "chain-of-thought, reasoning, drafting notes, topic analysis, headers, or "
    "English text. Return only the raw Marathi poem stanzas."
)

USER_PROMPT_TEMPLATE = """\
तू एक कुशल मराठी कवी आहेस. आजच्या पुण्यातील ठळक बातम्या खाली दिल्या आहेत:

{news_block}

वरील बातम्यांवर आधारित एक सुंदर, लयबद्ध आणि यमक जुळणारी मराठी कविता लिही.
खालील सूचना काटेकोरपणे पाळ:
- कोणतेही शीर्षक, मथळा किंवा प्रस्तावना लिहू नकोस.
- कोणताही इंग्रजी शब्द वापरू नकोस — फक्त शुद्ध, प्रमाण मराठी वापर.
- कवितेत अचूक ४ कडवी (stanzas) असावीत, प्रत्येक कडव्यात नेमक्या ४ ओळी (एकूण १६ ओळी).
- प्रत्येक ओळ स्वतंत्र नव्या ओळीवर लिही — दोन ओळी एकत्र जोडू नकोस.
- कडव्यांच्या दरम्यान फक्त एक रिकामी ओळ ठेव.
- यमक (rhyme) आणि वृत्त (meter) सांभाळ — कविता गेय आणि प्रवाही वाटली पाहिजे.
- कवितेत दिलेल्या सर्व बातम्यांचा प्रत्यक्ष उल्लेख किंवा संदर्भ यावा.
- तुझे विचार, मसुदा (draft) किंवा विषय-विश्लेषण अजिबात लिहू नकोस — फक्त अंतिम कविता दे.
"""

CORRECTIVE_SUFFIX = """\

लक्षात ठेव: उत्तरात फक्त आणि फक्त अंतिम कविता हवी. तुझे विचार, मसुदा, विषय-विश्लेषण
किंवा कोणताही इंग्रजी मजकूर अजिबात लिहू नकोस. प्रत्येक ओळ नव्या ओळीवर स्वतंत्रपणे
लिही — दोन ओळी एकत्र जोडू नकोस. उत्तरात नेमक्या १६ ओळी हव्यात (४ कडवी x ४ ओळी).
"""

# --- Post-processing / cleaning ---------------------------------------------

_MARKDOWN_ARTIFACT_RE = re.compile(r"[*#`_]+")
_LEADING_ENUM_RE = re.compile(r"^[\-•]?\s*[\d०-९]{1,2}[.\):]\s*")
_ENGLISH_LETTER_RE = re.compile(r"[A-Za-z]")
_DEVANAGARI_RE = re.compile(r"[ऀ-ॿ]")

# Reasoning/scratchpad leak markers, in both English and their common
# Devanagari transliterations (the model sometimes writes "टॉपिक" instead of
# "Topic", which wouldn't be caught by an English-letter check alone).
_REASONING_KEYWORDS = (
    "topic", "draft", "drafting", "note", "notes", "stanza", "analysis",
    "thought", "reasoning", "plan", "outline", "headline", "scratchpad",
    "टॉपिक", "ड्राफ्ट", "मसुदा", "नोंद", "विश्लेषण",
)


class PoemGenerationError(Exception):
    """Raised only when no attempt ever produced any usable Marathi poem text."""


def _build_news_block(articles: List[NewsArticle]) -> str:
    lines = []
    for idx, article in enumerate(articles, start=1):
        lines.append(f"{idx}. {article.as_text()}")
    return "\n".join(lines)


def _clean_line(raw_line: str) -> str:
    line = raw_line.strip()
    line = _MARKDOWN_ARTIFACT_RE.sub("", line)
    line = _LEADING_ENUM_RE.sub("", line)
    return line.strip()


def _looks_like_reasoning_leak(line: str) -> bool:
    lowered = line.lower()
    return any(keyword.lower() in lowered for keyword in _REASONING_KEYWORDS)


def _extract_devanagari_lines(raw_text: str) -> List[str]:
    """
    Clean raw model output into a list of poem lines: strip markdown
    artifacts and leading enumeration, then discard any line that leaks
    English/reasoning text or has no real Devanagari content.
    """
    cleaned_lines: List[str] = []
    for raw_line in raw_text.splitlines():
        line = _clean_line(raw_line)
        if not line:
            continue
        if _ENGLISH_LETTER_RE.search(line):
            continue
        if _looks_like_reasoning_leak(line):
            continue
        if not _DEVANAGARI_RE.search(line):
            continue
        cleaned_lines.append(line)
    return cleaned_lines


def _format_stanzas(lines: List[str]) -> str:
    """Group cleaned lines into 4-line stanzas separated by a blank line."""
    stanzas = [lines[i : i + LINES_PER_STANZA] for i in range(0, len(lines), LINES_PER_STANZA)]
    return "\n\n".join("\n".join(stanza) for stanza in stanzas)


# --- Generation ---------------------------------------------------------------


def generate_marathi_poem(articles: List[NewsArticle], api_key: str) -> str:
    """
    Generate a Marathi poem summarizing `articles` using Gemini, ideally
    exactly 16 lines (4 stanzas x 4 lines). Returns just the formatted poem
    text — no headline list is appended here.

    Attempts 1-3 require an exact 16-line cleaned result to succeed early.
    The final attempt relaxes to accepting 8-24 cleaned lines, and failing
    that, falls back to the best cleaned output seen across every attempt.
    PoemGenerationError is raised only if no attempt ever produced any
    usable Devanagari text at all.
    """
    if not articles:
        raise ValueError("Cannot generate a poem from an empty article list.")

    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(
        model_name=MODEL_NAME,
        system_instruction=SYSTEM_INSTRUCTION,
    )

    news_block = _build_news_block(articles)
    prompt = USER_PROMPT_TEMPLATE.format(news_block=news_block)

    best_lines: List[str] = []
    best_attempt = 0

    for attempt in range(1, MAX_GENERATION_ATTEMPTS + 1):
        current_prompt = prompt if attempt == 1 else prompt + CORRECTIVE_SUFFIX
        is_last_attempt = attempt == MAX_GENERATION_ATTEMPTS
        logger.info("Gemini poem generation attempt %d/%d", attempt, MAX_GENERATION_ATTEMPTS)

        raw_text = ""
        try:
            response = model.generate_content(
                current_prompt,
                generation_config={"temperature": 0.7},
            )
            raw_text = (response.text or "").strip()
        except ResourceExhausted as exc:
            logger.warning("Rate limit (429) hit on attempt %d/%d: %s", attempt, MAX_GENERATION_ATTEMPTS, exc)
            if not is_last_attempt:
                time.sleep(RETRY_DELAY_SECONDS)
                continue
        except Exception as exc:
            logger.warning("Gemini API call failed on attempt %d/%d: %s", attempt, MAX_GENERATION_ATTEMPTS, exc)
            if not is_last_attempt:
                time.sleep(RETRY_DELAY_SECONDS)
                continue

        cleaned_lines = _extract_devanagari_lines(raw_text)

        if cleaned_lines and (
            not best_lines or abs(len(cleaned_lines) - EXPECTED_LINES) < abs(len(best_lines) - EXPECTED_LINES)
        ):
            best_lines = cleaned_lines
            best_attempt = attempt

        if len(cleaned_lines) == EXPECTED_LINES:
            logger.info("Valid 16-line poem received on attempt %d", attempt)
            return _format_stanzas(cleaned_lines)

        logger.warning(
            "Attempt %d produced %d usable Devanagari lines after cleaning (expected %d).",
            attempt,
            len(cleaned_lines),
            EXPECTED_LINES,
        )

        if not is_last_attempt:
            time.sleep(RETRY_DELAY_SECONDS)
            continue

        # Final attempt: relax the requirement rather than failing the job.
        if MIN_FALLBACK_LINES <= len(best_lines) <= MAX_FALLBACK_LINES:
            logger.warning(
                "Falling back to a relaxed %d-line poem from attempt %d (expected exactly %d lines).",
                len(best_lines),
                best_attempt,
                EXPECTED_LINES,
            )
            return _format_stanzas(best_lines)

        if best_lines:
            logger.warning(
                "Falling back to the best-effort cleaned output (%d lines, from attempt %d) — "
                "outside the ideal range, but publishing beats failing the daily job.",
                len(best_lines),
                best_attempt,
            )
            return _format_stanzas(best_lines)

    raise PoemGenerationError(
        "Gemini never returned any usable Marathi (Devanagari) poem text across "
        f"{MAX_GENERATION_ATTEMPTS} attempts."
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    sample_articles = [
        NewsArticle(title="पुणे मेट्रोच्या नव्या मार्गिकेचे उद्घाटन", summary="प्रवाशांसाठी नवी सुविधा सुरू"),
        NewsArticle(title="पुण्यात पावसाचा जोर वाढला", summary="हवामान खात्याचा इशारा"),
        NewsArticle(title="पुणे विद्यापीठात नवीन अभ्यासक्रम सुरू", summary="विद्यार्थ्यांसाठी संधी"),
        NewsArticle(title="शहरातील रस्ते दुरुस्तीचे काम सुरू", summary="वाहतूक कोंडी टाळण्यासाठी उपाय"),
    ]

    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise SystemExit("Set GEMINI_API_KEY environment variable to test locally.")

    print(generate_marathi_poem(sample_articles, key))
