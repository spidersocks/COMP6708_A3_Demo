"""Unit tests for lexical off-topic gate (no live LLM)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rag.pipeline import _off_topic  # noqa: E402


def _hit(title: str, text: str, source: str = "en/courses/Phonics.md") -> dict:
    return {
        "metadata": {"title": title, "source": source, "canonical": title},
        "text": text,
        "score": 0.63,
    }


def test_phonics_lessons_not_off_topic():
    hit = _hit(
        "Phonics / Little Sound Explorers",
        "BrightNest Phonics builds letter-sound awareness for K1–P1.",
    )
    assert not _off_topic("Can you tell me more about your phonics lessons", hit)


def test_mandarin_classes_not_off_topic():
    hit = _hit(
        "Mandarin & Pinyin",
        "Our Mandarin programme covers tones and pinyin for ages 3–8.",
        "en/courses/MandarinPinyin.md",
    )
    assert not _off_topic("Tell me more about your Mandarin classes", hit)


def test_fish_vs_hours_is_off_topic():
    hit = _hit(
        "Opening hours",
        "We open Monday to Saturday 9am–6pm in Central Hong Kong.",
        "en/faq/opening_hours.md",
    )
    assert _off_topic("How do fish breathe underwater?", hit)


def test_generic_lessons_with_weak_hit_off_topic():
    hit = _hit(
        "Opening hours",
        "We open Monday to Saturday 9am–6pm.",
        "en/faq/opening_hours.md",
    )
    # "lessons" is a stopword; no programme cue shared with hours FAQ
    assert _off_topic("Can you tell me more about your lessons?", hit)
