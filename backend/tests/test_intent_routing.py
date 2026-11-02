"""Golden intent-routing tests — dates, weather, fees, and conflict cases."""
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
    ],
)
def test_weather_vs_hours(msg: str, expected: str):
    assert _route(msg) == expected


@pytest.mark.parametrize(
    "msg,expected",
    [
        ("How much is the phonics class?", "fees"),
        ("What are your tuition fees?", "fees"),
        ("How do I pay for a course package?", "payment"),
        ("FPS payment details?", "payment"),
        ("How much time is a trial?", "trial_info"),  # duration → trial facts, not fees
        ("Where are you located?", "location"),
        ("How do I get there by MTR?", "location"),
        ("My child has a peanut allergy", "allergy"),
        ("Fever of 39C — bring to class?", "illness"),
        ("Can my dog sit in on the trial?", "pets_policy"),
        ("Do you offer a trial for my 5 year old?", "trial_info"),
        ("If my kid has not attended before, does he need to book a trial assessment first?", "trial_info"),
        ("But do I need to book the trial assessment before class?", "trial_info"),
        ("Show trial class availability", "booking_tool"),
        ("I want to book a trial class", "booking_dialogue"),
        ("huh?", "confused"),
        ("Thanks!", "thanks"),
        ("Hello", "greeting"),
        ("Can I speak to a real person?", "staff_escalation"),
        ("This is unacceptable get me a manager", "staff_escalation"),
        ("Tell me about your teachers", "teachers"),
        ("Who can pick up my child?", "pickup"),
        ("What is your make-up class policy?", "makeup"),
        ("Do you have a sibling discount?", "sibling"),
        ("Is there a waiting list if a class is full?", "waiting"),
        ("黑色暴雨还上课吗？", "severe_weather"),
        ("你们老师有什么资格？", "teachers"),
        ("你哋喺邊？點搭港鐵？", "location"),
        ("點樣俾學費？有冇轉數快？", "payment"),
    ],
)
def test_core_intents(msg: str, expected: str):
    assert _route(msg) == expected


def test_change_teachers_not_roster():
    assert _route("Can we change teachers mid-term?") != "teachers"


def test_phonics_teachers_filtered_not_full_roster():
    from services import centre_facts

    answer, _ = centre_facts.format_teachers_answer(
        "en", message="which teachers do you have available to teach phonics?"
    )
    low = answer.lower()
    assert "avery" in low or "jordan" in low
    assert "wei lin" not in low  # Mandarin lead — not a phonics-tagged dump
    assert "sofia" not in low


def test_weather_beats_hours_in_scores():
    scores, debug = score_intents(
        "Are you open during T8?", lang="en", session_id="t"
    )
    assert "severe_weather" in scores
    assert "opening_hours" not in scores
    # hours_intent itself clears weather-only / T8+open as hours; router may not list a conflict
    assert decide_intent("Are you open during T8?", lang="en", session_id="t").route == (
        "severe_weather"
    )


def test_fees_not_duration():
    assert _route("How long is the phonics class?") != "fees"
