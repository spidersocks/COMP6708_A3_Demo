"""Golden intent-routing tests — tools stay hard; static FAQs → RAG."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.intent_router import decide_intent, score_intents  # noqa: E402


def _route(msg: str, lang: str = "en", sid: str = "test") -> str:
    return decide_intent(msg, lang=lang, session_id=sid).route


@pytest.mark.parametrize(
    "msg,expected",
    [
        ("What happens when there is a typhoon signal 8?", "severe_weather"),
        ("Are you open during typhoon signal 8?", "severe_weather"),
        ("Black rain — classes on?", "severe_weather"),
        ("Will class continue under typhoon signal 3?", "severe_weather"),
        ("八號風球會唔會停課？", "severe_weather"),
        ("What are your opening hours?", "opening_hours"),
        ("Are you open this Saturday at 10am?", "opening_hours"),
        ("Are you open tomorrow?", "opening_hours"),
        ("週末营业吗？", "opening_hours"),
        ("黑色暴雨还上课吗？", "severe_weather"),
    ],
)
def test_weather_vs_hours(msg: str, expected: str):
    assert _route(msg) == expected


@pytest.mark.parametrize(
    "msg",
    [
        "How much is the phonics class?",
        "What are your tuition fees?",
        "月費幾多？",
        "How do I pay for a course package?",
        "FPS payment details?",
        "Where are you located?",
        "How do I get there by MTR?",
        "My child has a peanut allergy",
        "Fever of 39C — bring to class?",
        "Can my dog sit in on the trial?",
        "Do you offer a trial for my 5 year old?",
        "How much time is a trial?",
        "Tell me about your teachers",
        "Who can pick up my child?",
        "What is your make-up class policy?",
        "Do you have a sibling discount?",
        "Is there a waiting list if a class is full?",
        "滿班嘅話可唔可以候補？",
        "你们老师有什么资格？",
        "你哋喺邊？點搭港鐵？",
        "點樣俾學費？有冇轉數快？",
        "Hello",
        "Thanks!",
        "huh?",
        "Can you tell me more about your phonics lessons",
    ],
)
def test_static_faqs_are_rag(msg: str):
    assert _route(msg) == "rag"


@pytest.mark.parametrize(
    "msg,expected",
    [
        ("Show trial class availability", "booking_tool"),
        ("I want to book a trial class", "booking_dialogue"),
        ("Can I speak to a real person?", "staff_escalation"),
        ("This is unacceptable get me a manager", "staff_escalation"),
        (
            "My child has a peanut allergy and had a reaction after class yesterday — please call me ASAP",
            "staff_escalation",
        ),
        ("How many seats are left in Saturday phonics?", "booking_tool"),
        ("How many seats are left and how much is phonics?", "booking_tool"),
        ("星期六拼音班仲有冇位？", "booking_tool"),
        ("拼音班有冇位？", "booking_tool"),
        ("What's the weather in Tokyo tomorrow?", "rag"),
        ("Please cancel my booking", "cancel_dialogue"),
    ],
)
def test_tool_intents(msg: str, expected: str):
    assert _route(msg) == expected


def test_weather_beats_hours_in_scores():
    scores, _debug = score_intents(
        "Are you open during T8?", lang="en", session_id="t"
    )
    assert "severe_weather" in scores
    assert "opening_hours" not in scores
    assert decide_intent("Are you open during T8?", lang="en", session_id="t").route == (
        "severe_weather"
    )
