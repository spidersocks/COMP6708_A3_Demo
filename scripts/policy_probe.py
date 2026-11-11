"""Live cheap-model probe: action policy on paraphrases (no full chat)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(ROOT))

from services import action_policy as ap  # noqa: E402

CASES = [
    ("book", "Hello I want to try out phonics for my child"),
    ("book", "Any chance of a taster for writing?"),
    ("book", "I'd like to get my son started on phonics"),
    ("book", "We should arrange a placement for my 4 year old"),
    ("book", "Put Terry down for Saturday phonics"),
    ("book", "Can we come in and see if mandarin suits my daughter?"),
    ("book", "Sign us up for the holiday intensive"),
    ("book", "我想幫小朋友試下拼音班"),
    ("book", "有冇得預約試堂"),
    ("availability", "How many seats are left in Saturday phonics?"),
    ("availability", "星期六拼音班仲有冇位？"),
    ("answer", "How much is the phonics class?"),
    ("answer", "What are your tuition fees?"),
    ("answer", "Tell me about your teachers"),
    ("answer", "Do I need a trial before joining?"),
    ("answer", "How long is a phonics lesson?"),
    ("answer", "Where are you located?"),
    ("answer", "My child has a peanut allergy"),
    ("hours", "Are you open this Saturday?"),
    ("hours", "What are your opening hours?"),
    ("weather", "Will class continue under typhoon signal 8?"),
    ("cancel", "Please cancel Jason's Saturday phonics"),
    ("cancel", "I need to drop my child's class this week"),
    ("escalate", "Can I speak to a real person?"),
    ("escalate", "This is unacceptable get me a manager"),
    ("answer", "Thanks!"),
    ("answer", "Hello"),
]


def main() -> None:
    bad = 0
    for expect, msg in CASES:
        verdict = ap.classify_turn(msg)
        act = verdict.get("act")
        conf = verdict.get("confidence")
        ok = act == expect
        # availability may reasonably be book; hours/weather/cancel/escalate/answer/book are strict
        if expect == "availability" and act in {"availability", "book"}:
            ok = True
        if not ok:
            bad += 1
        flag = "OK " if ok else "BAD"
        print(f"{flag} expect={expect:12} got={act:12} {conf}  {msg[:70]}")
    print(f"\n{bad}/{len(CASES)} mismatches")
    raise SystemExit(1 if bad else 0)


if __name__ == "__main__":
    main()
